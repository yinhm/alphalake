package duckdb

import (
	"context"
	"database/sql"
	_ "embed"
	"fmt"
)

const SchemaVersion = 46

//go:embed schema.sql
var schemaSQL string

// Initialize creates the current schema atomically in an empty database.
// Existing current databases are left unchanged; older/newer versions are rejected.
func Initialize(ctx context.Context, db *sql.DB) error {
	if db == nil {
		return fmt.Errorf("duckdb is nil")
	}
	version, err := CurrentSchemaVersion(ctx, db)
	if err != nil {
		return err
	}
	if version == SchemaVersion {
		return nil
	}
	if version != 0 {
		return fmt.Errorf("unsupported schema %d; current schema is %d; preserve evidence and rebuild or use the original implementation", version, SchemaVersion)
	}
	var objects int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM duckdb_tables() WHERE database_name=current_database() AND NOT internal`).Scan(&objects); err != nil {
		return err
	}
	if objects != 0 {
		return fmt.Errorf("refuse initialization of a nonempty unversioned database")
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err := tx.ExecContext(ctx, schemaSQL); err != nil {
		return fmt.Errorf("initialize current schema: %w", err)
	}
	return tx.Commit()
}

func CurrentSchemaVersion(ctx context.Context, db *sql.DB) (int, error) {
	if db == nil {
		return 0, fmt.Errorf("duckdb is nil")
	}
	var exists int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM information_schema.tables WHERE table_catalog=current_database() AND table_schema='meta' AND table_name='schema_version'`).Scan(&exists); err != nil {
		return 0, err
	}
	if exists == 0 {
		return 0, nil
	}
	var version int
	if err := db.QueryRowContext(ctx, `SELECT coalesce(max(version),0) FROM meta.schema_version`).Scan(&version); err != nil {
		return 0, err
	}
	return version, nil
}
