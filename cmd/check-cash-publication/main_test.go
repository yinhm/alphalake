package main

import (
	duck "github.com/yinhm/alphalake/internal/store/duckdb"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestCopyNewRejectsOverwriteAndBadHash(t *testing.T) {
	dir := t.TempDir()
	src := filepath.Join(dir, "source")
	dst := filepath.Join(dir, "copy")
	if err := os.WriteFile(src, []byte("accepted evidence"), 0600); err != nil {
		t.Fatal(err)
	}
	hash, err := fileHash(src)
	if err != nil {
		t.Fatal(err)
	}
	if err = copyNew(src, dst, hash); err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(src, []byte("changed evidence"), 0600); err != nil {
		t.Fatal(err)
	}
	if err = copyNew(src, dst, hash); err == nil {
		t.Fatal("existing backup overwritten")
	}
	after, err := fileHash(dst)
	if err != nil || after != hash {
		t.Fatal("existing copy changed", err)
	}
	if err = copyNew(src, filepath.Join(dir, "bad-copy"), hash); err == nil || !strings.Contains(err.Error(), "hash differs") {
		t.Fatal("corrupt copy accepted", err)
	}
}

func TestChangedBaselineRefusesBeforePublication(t *testing.T) {
	dir := t.TempDir()
	base := filepath.Join(dir, "base")
	receipt := filepath.Join(dir, "receipt.json")
	if err := os.WriteFile(base, []byte("changed database"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(receipt, []byte(`{"base_sha256":"wrong"}`), 0600); err != nil {
		t.Fatal(err)
	}
	before := os.Args
	defer func() { os.Args = before }()
	os.Args = []string{"check-cash-publication", base, filepath.Join(dir, "absent-candidate"), receipt, "--publish"}
	if err := run(); err == nil || !strings.Contains(err.Error(), "base changed") {
		t.Fatal("changed baseline accepted", err)
	}
	if _, err := os.Stat(base + ".pre-cash-history-20260911"); !os.IsNotExist(err) {
		t.Fatal("publication started", err)
	}
}

func TestNewFactSourceMismatch(t *testing.T) {
	ctx := t.Context()
	db, err := duck.Open(ctx, ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	_, err = db.ExecContext(ctx, `ATTACH ':memory:' AS baseline; ATTACH ':memory:' AS candidate;
 CREATE SCHEMA baseline.fundamental; CREATE SCHEMA candidate.fundamental;
 CREATE TABLE baseline.fundamental.fact(fact_id BIGINT);
 CREATE TABLE candidate.fundamental.fact AS SELECT 1 AS fact_id, 2 AS provider_fact_id, 'tdx' AS primary_source, 'FN230' AS source_provider_field, CAST(100 AS DECIMAL(38,10)) AS value, DATE '2024-03-31' AS report_period, '300866' AS provider_code, 3 AS instrument_id, 'proof' AS revision_key, 'revenue' AS canonical_field, 'CNY' AS unit;
 CREATE TABLE candidate.fundamental.provider_fact AS SELECT 2 AS provider_fact_id, 'tdx' AS source, 100.0 AS value, DATE '2024-03-31' AS report_period, '300866' AS provider_code, 3 AS instrument_id, 'FN230' AS provider_field, 'proof' AS revision_key;
 CREATE TABLE candidate.fundamental.provider_field AS SELECT 'tdx' AS source, 'FN230' AS provider_field, 1 AS value_multiplier, 'revenue' AS canonical_field, 'CNY' AS unit;`)
	if err != nil {
		t.Fatal(err)
	}
	count := func(want int) {
		t.Helper()
		var n int
		if err := db.QueryRowContext(ctx, newFactSourceMismatchSQL).Scan(&n); err != nil || n != want {
			t.Fatalf("source mismatch %d want %d: %v", n, want, err)
		}
	}
	count(0)
	for _, update := range []string{"value=value+1", "provider_code='600519'", "unit='USD'", "provider_fact_id=999", "revision_key='other'"} {
		if _, err = db.ExecContext(ctx, "BEGIN; UPDATE candidate.fundamental.fact SET "+update); err != nil {
			t.Fatal(err)
		}
		count(1)
		if _, err = db.ExecContext(ctx, "ROLLBACK"); err != nil {
			t.Fatal(err)
		}
	}
	count(0)
	if _, err = db.ExecContext(ctx, "UPDATE candidate.fundamental.provider_fact SET source='cninfo'"); err != nil {
		t.Fatal(err)
	}
	count(1)
}

func TestEarningsPublicationRequiresSealedCandidate(t *testing.T) {
	path := filepath.Join(t.TempDir(), "receipt.json")
	if err := os.WriteFile(path, []byte(`{"base_sha256":"base"}`), 0600); err != nil {
		t.Fatal(err)
	}
	before := os.Args
	defer func() { os.Args = before }()
	os.Args = []string{"check-cash-publication", "absent-base", "absent-candidate", path, "--earnings", "--publish"}
	if err := run(); err == nil || !strings.Contains(err.Error(), "candidate_sha256") {
		t.Fatal("unsealed publication accepted", err)
	}
}
