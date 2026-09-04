package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"

	_ "github.com/duckdb/duckdb-go/v2"
)

const DriverName = "duckdb"

// Open opens a DuckDB database at path and verifies that the connection is usable.
// Use :memory: for an in-memory database.
func Open(ctx context.Context, path string) (*sql.DB, error) {
	if strings.TrimSpace(path) == "" {
		return nil, errors.New("duckdb path is empty")
	}

	db, err := sql.Open(DriverName, path)
	if err != nil {
		return nil, fmt.Errorf("open duckdb %q: %w", path, err)
	}
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
