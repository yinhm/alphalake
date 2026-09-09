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
		"011_provider_record_resolution.sql",
		"012_provider_fact_raw_identity.sql",
		"013_filing_evidence.sql",
		"014_provider_filing_link.sql",
		"015_canonical_fundamental_fact.sql",
		"016_filing_link_lineage.sql",
		"017_filing_announcement_precision.sql",
		"018_core_financial_fields.sql",
		"019_tax_and_debt_fields.sql",
		"020_financial_windows.sql",
		"021_filing_translation.sql",
		"022_earnings_working_capital.sql",
		"023_cashflow_research_fields.sql",
		"024_balance_profit_fields.sql",
		"025_financial_instrument_fields.sql",
		"026_reviewed_supplements.sql",
		"027_wacc_reference_observations.sql",
		"028_credit_spread_bands.sql",
		"029_daily_observation_versions.sql",
		"030_market_capital.sql",
		"031_equity_proceeds.sql",
		"032_classification_freshness.sql",
		"033_provider_conflicts.sql",
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

func TestCoreFinancialMigrationFromV17(t *testing.T) {
	ctx := t.Context()
	db, err := Open(ctx, filepath.Join(t.TempDir(), "upgrade.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	migrations, err := Migrations()
	if err != nil {
		t.Fatal(err)
	}
	for _, m := range migrations[:17] {
		if err := applyMigration(ctx, db, m); err != nil {
			t.Fatal(err)
		}
	}
	for range 2 {
		if err := Apply(ctx, db); err != nil {
			t.Fatal(err)
		}
		var existing, instant, ytd, historical int
		if err := db.QueryRowContext(ctx, `SELECT
			count(*) FILTER (WHERE period_basis='report'),
			count(*) FILTER (WHERE period_basis='instant'),
			count(*) FILTER (WHERE period_basis='ytd'),
			count(*) FILTER (WHERE valid_from < DATE '2025-01-01')
			FROM fundamental.provider_field WHERE source='tdx'`).Scan(&existing, &instant, &ytd, &historical); err != nil {
			t.Fatal(err)
		}
		if existing != 9 || instant != 43 || ytd != 28 || historical != 9 {
			t.Fatalf("mapping upgrade: existing=%d instant=%d ytd=%d historical=%d", existing, instant, ytd, historical)
		}
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

func TestFilingPrecisionMigrationBackfillsLegacyRows(t *testing.T) {
	ctx := context.Background()
	db, err := Open(ctx, filepath.Join(t.TempDir(), "filing-precision.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	migrations, err := Migrations()
	if err != nil {
		t.Fatal(err)
	}
	for _, migration := range migrations[:16] {
		if err := applyMigration(ctx, db, migration); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := db.ExecContext(ctx, `INSERT INTO fundamental.filing (source, source_filing_id, announcement_time)
		VALUES ('legacy', 'legacy', TIMESTAMP '2026-03-28 10:00:00')`); err != nil {
		t.Fatal(err)
	}
	if err := Apply(ctx, db); err != nil {
		t.Fatal(err)
	}
	var date, precision string
	if err := db.QueryRowContext(ctx, `SELECT CAST(announcement_date AS VARCHAR), announcement_time_precision
		FROM fundamental.filing WHERE source='legacy'`).Scan(&date, &precision); err != nil {
		t.Fatal(err)
	}
	if date != "2026-03-28" || precision != "timestamp" {
		t.Fatalf("legacy timing=%s/%s", date, precision)
	}
}

func TestTranslationMigrationFromV20(t *testing.T) {
	ctx := t.Context()
	db, err := Open(ctx, filepath.Join(t.TempDir(), "translation.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	migrations, err := Migrations()
	if err != nil {
		t.Fatal(err)
	}
	for _, m := range migrations[:20] {
		if err := applyMigration(ctx, db, m); err != nil {
			t.Fatal(err)
		}
	}
	_, err = db.ExecContext(ctx, `INSERT INTO fundamental.filing (source,source_filing_id,title,report_period,filing_variant,classifier_version) VALUES
 ('cninfo','cn','贵州茅台2025年年度报告',DATE '2025-12-31','full','cninfo-periodic-title-v2'),
 ('cninfo','en','贵州茅台2025年年度报告（英文版）',DATE '2025-12-31','full','cninfo-periodic-title-v2')`)
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.ExecContext(ctx, `UPDATE fundamental.filing SET corrects_filing_id=(SELECT filing_id FROM fundamental.filing WHERE source_filing_id='en') WHERE source_filing_id='cn'`)
	if err != nil {
		t.Fatal(err)
	}
	if err := Apply(ctx, db); err != nil {
		t.Fatal(err)
	}
	var variant, version string
	if err := db.QueryRowContext(ctx, `SELECT filing_variant,classifier_version FROM fundamental.filing WHERE source_filing_id='en'`).Scan(&variant, &version); err != nil {
		t.Fatal(err)
	}
	if variant != "translation" || version != "cninfo-periodic-title-v3" {
		t.Fatal(variant, version)
	}
	if err := db.QueryRowContext(ctx, `SELECT filing_variant FROM fundamental.filing WHERE source_filing_id='cn'`).Scan(&variant); err != nil {
		t.Fatal(err)
	}
	if variant != "full" {
		t.Fatal(variant)
	}
	var n int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.filing WHERE corrects_filing_id IS NOT NULL`).Scan(&n); err != nil {
		t.Fatal(err)
	}
	if n != 0 {
		t.Fatal("unsupported translation predecessor retained")
	}
}
