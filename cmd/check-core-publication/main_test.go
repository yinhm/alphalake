package main

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	duck "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestPublicationRejectsChangesAndPreservesBackup(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	base := filepath.Join(dir, "base.duckdb")
	candidate := filepath.Join(dir, "candidate.duckdb")
	db, e := duck.Open(ctx, base)
	if e != nil {
		t.Fatal(e)
	}
	ms, e := duck.Migrations()
	if e != nil {
		t.Fatal(e)
	}
	for _, m := range ms[:39] {
		raw, e := duck.Read(m.Name)
		if e != nil {
			t.Fatal(e)
		}
		if _, e = db.ExecContext(ctx, string(raw)); e != nil {
			t.Fatal(e)
		}
		if _, e = db.ExecContext(ctx, `INSERT INTO meta.schema_version(version,description) VALUES (?,?) ON CONFLICT DO NOTHING`, m.Version, m.Description); e != nil {
			t.Fatal(e)
		}
	}
	if _, e = db.ExecContext(ctx, `INSERT INTO ref.instrument(instrument_type,name) VALUES('equity','original')`); e != nil {
		t.Fatal(e)
	}
	if e = db.Close(); e != nil {
		t.Fatal(e)
	}
	bytes, e := os.ReadFile(base)
	if e != nil {
		t.Fatal(e)
	}
	if e = os.WriteFile(candidate, bytes, 0600); e != nil {
		t.Fatal(e)
	}
	db, e = duck.OpenAndMigrate(ctx, candidate)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = db.ExecContext(ctx, `UPDATE core.instrument SET name='tampered'`); e != nil {
		t.Fatal(e)
	}
	db.Close()
	if e = run([]string{base, candidate}); e == nil {
		t.Fatal("changed identity accepted")
	}
	db, e = duck.Open(ctx, candidate)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = db.ExecContext(ctx, `UPDATE core.instrument SET name='original'`); e != nil {
		t.Fatal(e)
	}
	db.Close()
	if e = run([]string{base, candidate}); e != nil {
		t.Fatal(e)
	}
	if e = run([]string{base, candidate, "--publish"}); e == nil {
		t.Fatal("publication without pinned hashes accepted")
	}
	bh, _ := digest(base)
	ch, _ := digest(candidate)
	if e = run([]string{base, candidate, "--publish", "--base-sha256", bh, "--candidate-sha256", "bad"}); e == nil {
		t.Fatal("changed candidate hash accepted")
	}
	if e = run([]string{base, candidate, "--publish", "--base-sha256", bh, "--candidate-sha256", ch}); e != nil {
		t.Fatal(e)
	}
	got, e := digest(base + ".pre-core-risk-20260918")
	if e != nil || got != bh {
		t.Fatal("backup changed", e)
	}
	got, e = digest(base)
	if e != nil || got != ch {
		t.Fatal("publication changed candidate", e)
	}
	db, e = duck.OpenReadOnly(ctx, base)
	if e != nil {
		t.Fatal(e)
	}
	defer db.Close()
	v, e := duck.CurrentSchemaVersion(ctx, db)
	if e != nil || v != 44 {
		t.Fatal("published database cannot reopen", v, e)
	}
}
