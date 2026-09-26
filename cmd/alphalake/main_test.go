package main

import (
	"errors"
	"flag"
	"os"
	"path/filepath"
	"testing"
)

func TestHelpRequested(t *testing.T) {
	for _, c := range []struct {
		args []string
		want bool
	}{
		{nil, false},
		{[]string{"--help"}, true},
		{[]string{"-h"}, true},
		{[]string{"help"}, true},
		{[]string{"sync-filings", "--help"}, true},
		{[]string{"init", "-h"}, true},
		{[]string{"status", "help"}, true},
		{[]string{"sync-filings"}, false},
		{[]string{"sync-filings", "db.duckdb"}, false},
		{[]string{"sync-filings", "db.duckdb", "--metadata-only"}, false},
		{[]string{"sync-filings", "db.duckdb", "--code", "help"}, false},
	} {
		if got := helpRequested(c.args); got != c.want {
			t.Fatalf("helpRequested(%v)=%v want %v", c.args, got, c.want)
		}
	}
}

func TestParseErrorClassification(t *testing.T) {
	if err := parseError(nil); err != nil {
		t.Fatalf("nil: %v", err)
	}
	if err := parseError(flag.ErrHelp); !errors.Is(err, errHelp) {
		t.Fatalf("ErrHelp: %v", err)
	}
	if err := parseError(errors.New("bad flag")); !errors.Is(err, errUsage) {
		t.Fatalf("parse failure: %v", err)
	}
}

// 帮助请求与参数解析失败必须在打开数据库、触网之前被拒绝：
// sync-filings 误入 db-path 位置的 --help 曾建库并启动联网同步（回归）。
func TestSyncFilingsHelpAndParseFailureHaveNoSideEffects(t *testing.T) {
	dir := t.TempDir()
	t.Chdir(dir)
	ctx := t.Context()
	for _, args := range [][]string{
		{"sync-filings", "--help"},
		{"sync-filings", "-h"},
		{"sync-filings", "help"},
		{"sync-filings", "db.duckdb", "--help"},
		{"materialize-fundamentals", "--help"},
		{"repair-filings", "--help"},
	} {
		handled, err := runExtendedCommand(ctx, args)
		if !handled || !errors.Is(err, errHelp) {
			t.Fatalf("%v: handled=%v err=%v", args, handled, err)
		}
	}
	for _, args := range [][]string{
		{"sync-filings"},
		{"sync-filings", "db.duckdb", "--window-days", "abc"},
		{"sync-filings", "db.duckdb", "--bogus"},
		{"sync-filings", "db.duckdb", "extra"},
		{"sync-filings", "db.duckdb", "--all", "--start", "2020-01-01"},
		{"sync-filings", "db.duckdb", "--start", "not-a-date"},
		{"materialize-fundamentals"},
		{"materialize-fundamentals", "db.duckdb", "--bogus"},
		{"filing-unresolved", "db.duckdb", "--limit", "0"},
		{"repair-filings", "db.duckdb", "--period", "not-a-date"},
	} {
		handled, err := runExtendedCommand(ctx, args)
		if !handled || !errors.Is(err, errUsage) || errors.Is(err, errHelp) {
			t.Fatalf("%v: handled=%v err=%v", args, handled, err)
		}
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 0 {
		names := make([]string, len(entries))
		for i, e := range entries {
			names[i] = e.Name()
		}
		t.Fatalf("help/parse failure created files: %v", names)
	}
	if _, err := os.Stat(filepath.Join(dir, "db.duckdb")); !os.IsNotExist(err) {
		t.Fatalf("database file created: %v", err)
	}
}
