package duckdb

import (
	"path/filepath"
	"testing"
)

func TestUpgradeEarningsGuidancePreservesFinancialFacts(t *testing.T) {
	path := filepath.Join(t.TempDir(), "upgrade.duckdb")
	db, err := OpenInitialized(t.Context(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { db.Close() }()
	if _, err = db.Exec(`DROP VIEW fundamental.current_earnings_guidance; DROP TABLE fundamental.earnings_guidance_relation; DROP TABLE fundamental.earnings_guidance_observation; UPDATE meta.schema_version SET version=57; INSERT INTO fundamental.statement_snapshot(source_record_id,instrument_id,report_period,ingest_run_id,revenue) VALUES(1,1,'2026-06-30',1,123.5)`); err != nil {
		t.Fatal(err)
	}
	if err = Initialize(t.Context(), db); err == nil {
		t.Fatal("old database silently accepted")
	}
	if err = UpgradeEarningsGuidance(t.Context(), db); err != nil {
		t.Fatal(err)
	}
	if version, err := CurrentSchemaVersion(t.Context(), db); err != nil || version != 58 {
		t.Fatal(version, err)
	}
	var value float64
	if err = db.QueryRow(`SELECT revenue FROM fundamental.statement_snapshot`).Scan(&value); err != nil || value != 123.5 {
		t.Fatal(value, err)
	}
	if err = UpgradeEarningsGuidance(t.Context(), db); err == nil {
		t.Fatal("repeat upgrade accepted")
	}
	if err = db.Close(); err != nil {
		t.Fatal(err)
	}
	db, err = OpenInitialized(t.Context(), path)
	if err != nil {
		t.Fatal(err)
	}
	if err = db.QueryRow(`SELECT revenue FROM fundamental.statement_snapshot`).Scan(&value); err != nil || value != 123.5 {
		t.Fatal(value, err)
	}
}
