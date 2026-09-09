package duckdb

import (
	"context"
	"path/filepath"
	"strings"
	"testing"
)

func TestOpenUsesNativeResourceLimits(t *testing.T) {
	t.Setenv("ALPHALAKE_DUCKDB_MEMORY_LIMIT", "256MiB")
	t.Setenv("ALPHALAKE_DUCKDB_THREADS", "2")
	for _, path := range []string{":memory:", filepath.Join(t.TempDir(), "bounded.duckdb")} {
		db, err := Open(context.Background(), path)
		if err != nil {
			t.Fatal(err)
		}
		var memory string
		var threads int
		err = db.QueryRow(`SELECT current_setting('memory_limit'),current_setting('threads')`).Scan(&memory, &threads)
		db.Close()
		if err != nil || !strings.HasPrefix(memory, "256") || threads != 2 {
			t.Fatalf("resource settings: %s %d %v", memory, threads, err)
		}
	}
	t.Setenv("ALPHALAKE_DUCKDB_MEMORY_LIMIT", "not-a-size")
	if db, err := Open(context.Background(), ":memory:"); err == nil {
		db.Close()
		t.Fatal("invalid memory limit silently accepted")
	}
}

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
