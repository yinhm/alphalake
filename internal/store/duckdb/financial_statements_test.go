package duckdb

import (
	"encoding/json"
	"path/filepath"
	"strconv"
	"testing"
	"time"
)

func TestStatementSnapshotBoundaries(t *testing.T) {
	ctx := t.Context()
	db, e := OpenInitialized(ctx, filepath.Join(t.TempDir(), "statements.duckdb"))
	if e != nil {
		t.Fatal(e)
	}
	defer db.Close()
	check := func(e error) {
		t.Helper()
		if e != nil {
			t.Fatal(e)
		}
	}
	_, e = db.ExecContext(ctx, `INSERT INTO core.instrument(instrument_id,instrument_type) VALUES(9007199254740993,'equity');
 INSERT INTO core.instrument_identifier(instrument_id,provider,identifier_type,identifier_value,valid_from) VALUES(9007199254740993,'tdx','symbol','sz000001','2020-01-01');
 `)
	check(e)
	first := seedStandardSnapshot(t, db, 9007199254740993, "000001", "tdx", "notes_receivable", "2026-06-30", "2026-08-01", 100)
	second := seedStandardSnapshot(t, db, 9007199254740993, "000001", "tdx", "notes_receivable", "2026-06-30", "2026-09-01", 200)
	check(e)
	period := time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC)
	for _, tc := range []struct {
		at     string
		status string
		want   any
	}{{"2026-07-31T23:59:59Z", "no_linked_source_at_asof", nil}, {"2026-08-01T00:00:00Z", "available", "100.0000000000"}, {"2026-09-01T00:00:00Z", "available", "200.0000000000"}} {
		at, e := time.Parse(time.RFC3339, tc.at)
		check(e)
		out, e := ExportFinancialStatements(ctx, db, "000001", period, at, true)
		check(e)
		found := false
		for _, row := range out["statements"].(map[string][]map[string]any)["balance_sheet"] {
			if row["field"] != "notes_receivable" {
				continue
			}
			found = true
			if row["status"] != tc.status || row["value"] != tc.want || row["instrument_id"] != json.Number("9007199254740993") {
				t.Fatal(row)
			}
			if tc.want != nil && row["source_evidence"].(map[string]any)["fact_id"] != map[string]json.Number{"100.0000000000": json.Number(strconv.FormatInt(first, 10)), "200.0000000000": json.Number(strconv.FormatInt(second, 10))}[tc.want.(string)] {
				t.Fatal("lineage rounded", row)
			}
		}
		if !found {
			t.Fatal("missing statement item")
		}
	}
	at := time.Date(2026, 9, 19, 0, 0, 0, 0, time.UTC)
	// A latest corrupted standard observation is diagnosed, not replaced by an older value.
	_, e = db.ExecContext(ctx, `UPDATE fundamental.statement_field SET unit='USD' WHERE canonical_field='notes_receivable'`)
	check(e)
	var status string
	var value *string
	check(db.QueryRowContext(ctx, `SELECT status,CAST(value AS VARCHAR) FROM fundamental.statements_asof(?,?,?) WHERE field='notes_receivable'`, at, period, "000001").Scan(&status, &value))
	if status != "standard_fact_semantics_mismatch" || value != nil {
		t.Fatal(status, value)
	}
	artifact := insertTestArtifact(t, ctx, db, "statement-conflict", at.Add(-time.Hour))
	_, e = db.ExecContext(ctx, `INSERT INTO fundamental.provider_record_resolution(artifact_id,source,source_file,report_period,provider_code,market_marker,status,reason) VALUES(?,'tdx','real-shape-conflict','2026-06-30','000001',0,'pending','conflicting duplicate provider records: synthetic boundary')`, artifact)
	check(e)
	var n int
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.statements_asof(?,?,?) WHERE status='source_conflict' AND value IS NULL`, at, period, "000001").Scan(&n))
	if n != 282 {
		t.Fatal("source conflict lost", n)
	}
	_, e = db.ExecContext(ctx, `INSERT INTO core.instrument(instrument_id,instrument_type) VALUES(2,'equity');INSERT INTO core.instrument_identifier(instrument_id,provider,identifier_type,identifier_value,valid_from) VALUES(2,'tdx','symbol','sh000001','2026-01-01')`)
	check(e)
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.statements_asof(?,?,?) WHERE status='ambiguous_identity' AND value IS NULL`, at, period, "000001").Scan(&n))
	if n != 282 {
		t.Fatal("identity guessed", n)
	}
	for _, p := range []time.Time{time.Time{}, period.AddDate(0, 0, -1)} {
		if _, e = ExportFinancialStatements(ctx, db, "000001", p, at, false); e == nil {
			t.Fatal("invalid period accepted")
		}
	}
	if _, e = ExportFinancialStatements(ctx, db, "FN10", period, at, false); e == nil {
		t.Fatal("raw source field used as company")
	}
}
