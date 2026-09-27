package main

import (
	"crypto/sha256"
	store "github.com/yinhm/alphalake/internal/store/duckdb"
	"os"
	"path/filepath"
	"testing"
)

func TestCapitalHistoryCopyKeepsSourceAndRejectsOverwrite(t *testing.T) {
	ctx := t.Context()
	dir := t.TempDir()
	t.Setenv("ALPHALAKE_WORKSPACE", dir)
	source := filepath.Join(dir, "source.duckdb")
	output := filepath.Join(dir, "candidate.duckdb")
	db, err := store.OpenInitialized(ctx, source)
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.ExecContext(ctx, `DELETE FROM fundamental.provider_field WHERE notes LIKE 'official-capital-history-v1;%'; DELETE FROM fundamental.statement_field WHERE notes LIKE 'official-capital-history-v1;%'`)
	if err != nil {
		t.Fatal(err)
	}
	if err = db.Close(); err != nil {
		t.Fatal(err)
	}
	digest := func(p string) [32]byte {
		t.Helper()
		b, e := os.ReadFile(p)
		if e != nil {
			t.Fatal(e)
		}
		return sha256.Sum256(b)
	}
	before := digest(source)
	if err = runCapitalHistory(ctx, []string{source, output}); err != nil {
		t.Fatal(err)
	}
	if digest(source) != before {
		t.Fatal("source mutated")
	}
	db, err = store.OpenReadOnly(ctx, output)
	if err != nil {
		t.Fatal(err)
	}
	var count int
	if err = db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.statement_field WHERE notes LIKE 'official-capital-history-v1;%'`).Scan(&count); err != nil || count != 19 {
		t.Fatal(count, err)
	}
	db.Close()
	candidateHash := digest(output)
	if err = runCapitalHistory(ctx, []string{source, output}); err == nil {
		t.Fatal("existing output overwritten")
	}
	if digest(output) != candidateHash || digest(source) != before {
		t.Fatal("overwrite failure mutated files")
	}
	if err = runCapitalHistory(ctx, []string{source, source}); err == nil {
		t.Fatal("source overwrite accepted")
	}
}
