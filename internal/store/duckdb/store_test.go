package duckdb

import (
	"context"
	"path/filepath"
	"testing"
)

func TestOpenRejectsEmptyPath(t *testing.T) {
	if _, err := Open(context.Background(), ""); err == nil {
		t.Fatal("Open() expected an error for an empty path")
	}
}

func TestOpenAndMigrate(t *testing.T) {
	path := filepath.Join(t.TempDir(), "alphalake.duckdb")
	db, err := OpenAndMigrate(context.Background(), path)
	if err != nil {
		t.Fatalf("OpenAndMigrate() error = %v", err)
	}
	defer db.Close()

	var count int
	err = db.QueryRow(`
		SELECT count(*)
		FROM information_schema.tables
		WHERE table_schema = 'market' AND table_name = 'ohlcv_daily'
	`).Scan(&count)
	if err != nil {
		t.Fatalf("query migrated schema: %v", err)
	}
	if count != 1 {
		t.Fatalf("market.ohlcv_daily count = %d, want 1", count)
	}
}
