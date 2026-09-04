package duckdb

import (
	"context"
	"database/sql"
	"database/sql/driver"
	"errors"
	"fmt"
	"path/filepath"
	"strings"

	duckdbgo "github.com/duckdb/duckdb-go/v2"
)

const (
	DriverName       = "duckdb"
	PersistentCatalog = "alphalake"
)

// Open opens a DuckDB database and verifies that the connection is usable.
//
// Persistent files are deliberately attached to an in-memory DuckDB instance
// under the stable catalog alias "alphalake" instead of being opened as the
// default file-derived catalog. DuckDB treats a two-part name like
// `classification.taxonomy` as ambiguous when the catalog and schema share the
// same name. A fixed catalog makes AlphaLake's domain schemas independent of the
// user's database filename (e.g. classification.duckdb is safe).
//
// Use :memory: for a purely in-memory database.
func Open(ctx context.Context, path string) (*sql.DB, error) {
	path = strings.TrimSpace(path)
	if path == "" {
		return nil, errors.New("duckdb path is empty")
	}

	var connector *duckdbgo.Connector
	var err error
	if path == ":memory:" {
		connector, err = duckdbgo.NewConnector(":memory:", nil)
	} else {
		absolutePath, absErr := filepath.Abs(path)
		if absErr != nil {
			return nil, fmt.Errorf("resolve duckdb path %q: %w", path, absErr)
		}
		attachSQL := fmt.Sprintf("ATTACH IF NOT EXISTS %s AS %s", duckdbStringLiteral(absolutePath), PersistentCatalog)
		connector, err = duckdbgo.NewConnector(":memory:", func(execer driver.ExecerContext) error {
			if _, err := execer.ExecContext(context.Background(), attachSQL, nil); err != nil {
				return fmt.Errorf("attach AlphaLake database %q: %w", absolutePath, err)
			}
			if _, err := execer.ExecContext(context.Background(), "USE "+PersistentCatalog+".main", nil); err != nil {
				return fmt.Errorf("select AlphaLake catalog: %w", err)
			}
			return nil
		})
	}
	if err != nil {
		return nil, fmt.Errorf("open duckdb %q: %w", path, err)
	}

	db := sql.OpenDB(connector)
	if err := db.PingContext(ctx); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("ping duckdb %q: %w", path, err)
	}
	return db, nil
}

// OpenAndMigrate opens a DuckDB database and applies AlphaLake's embedded schema.
func OpenAndMigrate(ctx context.Context, path string) (*sql.DB, error) {
	db, err := Open(ctx, path)
	if err != nil {
		return nil, err
	}
	if err := Apply(ctx, db); err != nil {
		_ = db.Close()
		return nil, err
	}
	return db, nil
}

func duckdbStringLiteral(value string) string {
	return "'" + strings.ReplaceAll(value, "'", "''") + "'"
}
