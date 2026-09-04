package duckdb

import (
	"context"
	"database/sql"
	"embed"
	"fmt"
	"io/fs"
	"sort"
	"strconv"
	"strings"
)

//go:embed migrations/*.sql
var migrationFS embed.FS

type Migration struct {
	Version     int
	Name        string
	Description string
}

func Names() ([]string, error) {
	entries, err := fs.ReadDir(migrationFS, "migrations")
	if err != nil {
		return nil, err
	}
	names := make([]string, 0, len(entries))
	for _, e := range entries {
		if !e.IsDir() {
			names = append(names, e.Name())
		}
	}
	sort.Strings(names)
	return names, nil
}

func Read(name string) ([]byte, error) {
	return migrationFS.ReadFile("migrations/" + name)
}

func Migrations() ([]Migration, error) {
	names, err := Names()
	if err != nil {
		return nil, err
	}
	out := make([]Migration, 0, len(names))
	for _, name := range names {
		migration, err := parseMigrationName(name)
		if err != nil {
			return nil, err
		}
		out = append(out, migration)
	}
	for i, migration := range out {
		want := i + 1
		if migration.Version != want {
			return nil, fmt.Errorf("migration sequence gap: %s has version %d, want %d", migration.Name, migration.Version, want)
		}
	}
	return out, nil
}

// Apply upgrades db to the latest embedded schema. Every migration is applied in
// its own transaction and recorded in meta.schema_version only after its SQL has
// succeeded. Existing pre-versioned AlphaLake databases contain version 1 only;
// migrations 2-6 are intentionally idempotent and will be replayed once while
// being registered, after which normal version gating applies.
func Apply(ctx context.Context, db *sql.DB) error {
	if db == nil {
		return fmt.Errorf("duckdb is nil")
	}
	migrations, err := Migrations()
	if err != nil {
		return err
	}
	applied, err := appliedMigrationVersions(ctx, db)
	if err != nil {
		return err
	}
	for _, migration := range migrations {
		if applied[migration.Version] {
			continue
		}
		if err := applyMigration(ctx, db, migration); err != nil {
			return err
		}
		applied[migration.Version] = true
	}
	return nil
}

func CurrentSchemaVersion(ctx context.Context, db *sql.DB) (int, error) {
	if db == nil {
		return 0, fmt.Errorf("duckdb is nil")
	}
	applied, err := appliedMigrationVersions(ctx, db)
	if err != nil {
		return 0, err
	}
	latest := 0
	for version := range applied {
		if version > latest {
			latest = version
		}
	}
	return latest, nil
}

func applyMigration(ctx context.Context, db *sql.DB, migration Migration) error {
	body, err := Read(migration.Name)
	if err != nil {
		return fmt.Errorf("read migration %s: %w", migration.Name, err)
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("begin migration %s: %w", migration.Name, err)
	}
	defer tx.Rollback()
	if _, err := tx.ExecContext(ctx, string(body)); err != nil {
		return fmt.Errorf("apply migration %s: %w", migration.Name, err)
	}
	// 001_meta.sql historically records version 1 itself. The conditional insert
	// keeps that migration backward compatible while making all later versions
	// runner-owned.
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO meta.schema_version(version, description)
		SELECT ?, ?
		WHERE NOT EXISTS (
			SELECT 1 FROM meta.schema_version WHERE version=?
		)
	`, migration.Version, migration.Description, migration.Version); err != nil {
		return fmt.Errorf("record migration %s: %w", migration.Name, err)
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("commit migration %s: %w", migration.Name, err)
	}
	return nil
}

func appliedMigrationVersions(ctx context.Context, db *sql.DB) (map[int]bool, error) {
	var exists int
	if err := db.QueryRowContext(ctx, `
		SELECT count(*)
		FROM information_schema.tables
		WHERE table_schema='meta' AND table_name='schema_version'
	`).Scan(&exists); err != nil {
		return nil, fmt.Errorf("detect schema version table: %w", err)
	}
	out := make(map[int]bool)
	if exists == 0 {
		return out, nil
	}
	rows, err := db.QueryContext(ctx, `SELECT version FROM meta.schema_version ORDER BY version`)
	if err != nil {
		return nil, fmt.Errorf("query schema versions: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var version int
		if err := rows.Scan(&version); err != nil {
			return nil, fmt.Errorf("scan schema version: %w", err)
		}
		out[version] = true
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate schema versions: %w", err)
	}
	return out, nil
}

func parseMigrationName(name string) (Migration, error) {
	if !strings.HasSuffix(name, ".sql") {
		return Migration{}, fmt.Errorf("migration %q must end in .sql", name)
	}
	stem := strings.TrimSuffix(name, ".sql")
	parts := strings.SplitN(stem, "_", 2)
	if len(parts) != 2 || len(parts[0]) != 3 || parts[1] == "" {
		return Migration{}, fmt.Errorf("invalid migration filename %q; want NNN_description.sql", name)
	}
	version, err := strconv.Atoi(parts[0])
	if err != nil || version <= 0 {
		return Migration{}, fmt.Errorf("invalid migration version in %q", name)
	}
	return Migration{
		Version:     version,
		Name:        name,
		Description: strings.ReplaceAll(parts[1], "_", " "),
	}, nil
}
