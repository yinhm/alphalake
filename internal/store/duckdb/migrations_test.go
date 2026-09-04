package duckdb

import (
	"context"
	"path/filepath"
	"testing"
)

func TestMigrationOrder(t *testing.T) {
	migrations, err := Migrations()
	if err != nil {
		t.Fatal(err)
	}
	want := []string{
		"001_meta.sql",
		"002_ref.sql",
		"003_market.sql",
		"004_fundamental.sql",
		"005_classification.sql",
		"006_adjustment_lineage.sql",
	}
	if len(migrations) != len(want) {
		t.Fatalf("got %v", migrations)
	}
	for i := range want {
		if migrations[i].Version != i+1 || migrations[i].Name != want[i] {
			t.Fatalf("migration %d = %#v, want version=%d name=%q", i, migrations[i], i+1, want[i])
		}
	}
}

func TestApplyRecordsEachMigrationOnce(t *testing.T) {
	ctx := context.Background()
	db, err := Open(ctx, filepath.Join(t.TempDir(), "migrations.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	if err := Apply(ctx, db); err != nil {
		t.Fatalf("first Apply() error = %v", err)
	}
	if err := Apply(ctx, db); err != nil {
		t.Fatalf("second Apply() error = %v", err)
	}

	var count int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM meta.schema_version`).Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 6 {
		t.Fatalf("schema version rows = %d, want 6", count)
	}
	version, err := CurrentSchemaVersion(ctx, db)
	if err != nil {
		t.Fatal(err)
	}
	if version != 6 {
		t.Fatalf("CurrentSchemaVersion() = %d, want 6", version)
	}
}

func TestApplyRegistersLegacyReplayOnlyDatabase(t *testing.T) {
	ctx := context.Background()
	db, err := Open(ctx, filepath.Join(t.TempDir(), "legacy.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	// Reproduce the pre-version-gating behavior: all embedded migrations were
	// replayed on startup, while only 001_meta.sql wrote schema_version.
	names, err := Names()
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range names {
		body, err := Read(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := db.ExecContext(ctx, string(body)); err != nil {
			t.Fatalf("legacy replay %s: %v", name, err)
		}
	}
	var before int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM meta.schema_version`).Scan(&before); err != nil {
		t.Fatal(err)
	}
	if before != 1 {
		t.Fatalf("legacy schema version rows = %d, want 1", before)
	}

	if err := Apply(ctx, db); err != nil {
		t.Fatalf("Apply() legacy upgrade error = %v", err)
	}
	var after int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM meta.schema_version`).Scan(&after); err != nil {
		t.Fatal(err)
	}
	if after != len(names) {
		t.Fatalf("schema version rows after upgrade = %d, want %d", after, len(names))
	}
}
