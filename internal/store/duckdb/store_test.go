package duckdb

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
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

func TestOpenInitialized(t *testing.T) {
	path := filepath.Join(t.TempDir(), "alphalake.duckdb")
	db, err := OpenInitialized(context.Background(), path)
	if err != nil {
		t.Fatalf("OpenInitialized() error = %v", err)
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

// 父进程保持连接，子进程读取同一文件；不是同进程连接池的伪并发。
func TestOpenReadOnlyAcrossProcesses(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if path := os.Getenv("ALPHALAKE_TEST_READONLY_DATABASE"); path != "" {
		db, err := OpenReadOnly(ctx, path)
		if err != nil {
			t.Fatal(err)
		}
		defer db.Close()
		var value int
		if err := db.QueryRowContext(ctx, "SELECT n FROM proof").Scan(&value); err != nil || value != 7 {
			t.Fatalf("read value=%d error=%v", value, err)
		}
		if _, err := db.ExecContext(ctx, "INSERT INTO proof VALUES (8)"); err == nil {
			t.Fatal("read-only connection allowed a write")
		}
		return
	}
	path := filepath.Join(t.TempDir(), "classification.duckdb")
	db, err := Open(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.ExecContext(ctx, "CREATE TABLE proof AS SELECT 7 AS n"); err != nil {
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	reader, err := OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestOpenReadOnlyAcrossProcesses$", "-test.count=1")
	cmd.Env = append(os.Environ(), "ALPHALAKE_TEST_READONLY_DATABASE="+path)
	if output, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("concurrent child reader: %v: %s", err, output)
	}
	if err := reader.Close(); err != nil {
		t.Fatal(err)
	}
	db, err = Open(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if _, err := db.ExecContext(ctx, "INSERT INTO proof VALUES (9)"); err != nil {
		t.Fatalf("writer after readers close: %v", err)
	}
	missing := filepath.Join(t.TempDir(), "missing.duckdb")
	for _, invalid := range []string{"", ":memory:", missing} {
		if got, err := OpenReadOnly(ctx, invalid); err == nil {
			got.Close()
			t.Fatalf("read-only accepted %q", invalid)
		}
	}
	if _, err := os.Stat(missing); !os.IsNotExist(err) {
		t.Fatalf("read-only created missing database: %v", err)
	}
}
