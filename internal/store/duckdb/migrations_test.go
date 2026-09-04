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
		"007_share_capital_identity.sql",
		"008_derived_state.sql",
		"009_fundamental_provider_raw.sql",
		"010_tdx_provider_field_catalog.sql",
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

	migrations, err := Migrations()
	if err != nil {
		t.Fatal(err)
	}
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
	if count != len(migrations) {
		t.Fatalf("schema version rows = %d, want %d", count, len(migrations))
	}
	version, err := CurrentSchemaVersion(ctx, db)
	if err != nil {
		t.Fatal(err)
	}
	if version != len(migrations) {
		t.Fatalf("CurrentSchemaVersion() = %d, want %d", version, len(migrations))
	}
}

func TestApplyRegistersLegacyReplayOnlyDatabase(t *testing.T) {
	ctx := context.Background()
	db, err := Open(ctx, filepath.Join(t.TempDir(), "legacy.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	migrations, err := Migrations()
	if err != nil {
		t.Fatal(err)
	}
	// Reproduce the pre-version-gating repository state: migrations 001-006
	// existed and were replayed on every startup, while only 001_meta.sql wrote
	// schema_version. Newer migrations must execute exactly once through Apply.
	for _, migration := range migrations[:6] {
		body, err := Read(migration.Name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := db.ExecContext(ctx, string(body)); err != nil {
			t.Fatalf("legacy replay %s: %v", migration.Name, err)
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
	if after != len(migrations) {
		t.Fatalf("schema version rows after upgrade = %d, want %d", after, len(migrations))
	}
}

func TestShareCapitalIdentityAllowsMultipleSourceRecordsPerDay(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "share-capital.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	for _, recordID := range []string{"event-a", "event-b"} {
		if _, err := db.ExecContext(ctx, `
			INSERT INTO market.share_capital (
				instrument_id, effective_date, float_shares, total_shares,
				source_category, source, source_record_id
			) VALUES (1, DATE '2026-09-04', 100, 200, 5, 'tdx', ?)
		`, recordID); err != nil {
			t.Fatalf("insert %s: %v", recordID, err)
		}
	}
	var count int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM market.share_capital`).Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 2 {
		t.Fatalf("share capital rows = %d, want 2", count)
	}
}
