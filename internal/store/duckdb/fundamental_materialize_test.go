package duckdb

import (
	"context"
	"database/sql"
	"github.com/yinhm/alphalake/internal/domain"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func linkedFinancialFixture(t *testing.T) (*sql.DB, domain.ProviderFinancialRecord, string, string) {
	t.Helper()
	ctx := t.Context()
	db, e := OpenInitialized(ctx, filepath.Join(t.TempDir(), "financial.duckdb"))
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { db.Close() })
	period := time.Date(2025, 12, 31, 0, 0, 0, 0, time.UTC)
	if _, e = db.ExecContext(ctx, `DELETE FROM fundamental.provider_field WHERE provider_field NOT IN('FN230','FN439','FN581')`); e != nil {
		t.Fatal(e)
	}
	r, sha, path := archiveFinancialFixture(t, ctx, db, "300866", 1, period, map[int]float32{230: 100, 439: 309.8})
	if _, e = ReconcileFinancialSourceRecords(ctx, db, 1, "tdx", sha, []domain.ProviderFinancialRecord{r}); e != nil {
		t.Fatal(e)
	}
	_, e = db.ExecContext(ctx, `INSERT INTO fundamental.filing(filing_id,instrument_id,source,source_filing_id,provider_code,report_period,announcement_time,filing_type,filing_variant,resolution_status) VALUES(1,1,'cninfo','annual','300866','2025-12-31','2026-03-01','annual','full','resolved')`)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = RefreshProviderFilingLinks(ctx, db, 1, "tdx"); e != nil {
		t.Fatal(e)
	}
	return db, r, sha, path
}

func TestMaterializeCanonicalFundamentalsNoLookAheadAndCorrection(t *testing.T) {
	db, _, _, _ := linkedFinancialFixture(t)
	ctx := t.Context()
	first, e := MaterializeCanonicalFundamentals(ctx, db, 2, "tdx")
	if e != nil || first.Materialized != 2 || first.Inserted != 2 || first.Rejected == 0 {
		t.Fatal(first, e)
	}
	for _, tc := range []struct {
		at string
		n  int
	}{{"2026-02-28T23:59:59Z", 0}, {"2026-03-01T00:00:00Z", 2}} {
		var n int
		e = db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.financial_observations_asof('300866',NULL,NULL,?)`, tc.at).Scan(&n)
		if e != nil || n != tc.n {
			t.Fatal(n, e)
		}
	}
	replay, e := MaterializeCanonicalFundamentals(ctx, db, 3, "tdx")
	if e != nil || (replay.Inserted != 0 || replay.Updated != 0 || replay.Removed != 0 || replay.Materialized != 2) {
		t.Fatal(replay, e)
	}
	var run int
	if e = db.QueryRowContext(ctx, `SELECT ingest_run_id FROM fundamental.statement_snapshot`).Scan(&run); e != nil || run != 2 {
		t.Fatal(run, e)
	}
	if _, e = db.ExecContext(ctx, `UPDATE fundamental.filing SET announcement_time='2026-03-02'`); e != nil {
		t.Fatal(e)
	}
	changed, e := MaterializeCanonicalFundamentals(ctx, db, 4, "tdx")
	if e != nil || changed.Updated != 0 {
		t.Fatal(changed, e)
	}
	var n int
	e = db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.financial_observations_asof('300866',NULL,NULL,'2026-03-01')`).Scan(&n)
	if e != nil || n != 0 {
		t.Fatal(n, e)
	}
}

func TestMaterializeCanonicalFundamentalsRejectsInvalidAndRemovesStale(t *testing.T) {
	db, _, _, path := linkedFinancialFixture(t)
	ctx := t.Context()
	if _, e := MaterializeCanonicalFundamentals(ctx, db, 2, "tdx"); e != nil {
		t.Fatal(e)
	}
	for _, tc := range []struct {
		mult              int
		removed, inserted int
	}{{-1, 1, 0}, {10000, 0, 1}} {
		if _, e := db.ExecContext(ctx, `UPDATE fundamental.provider_field SET value_multiplier=? WHERE canonical_field='lease_liabilities'`, tc.mult); e != nil {
			t.Fatal(e)
		}
		out, e := MaterializeCanonicalFundamentals(ctx, db, 3, "tdx")
		if e != nil || out.Removed != tc.removed || out.Inserted != tc.inserted {
			t.Fatal(out, e)
		}
	}
	if _, e := db.ExecContext(ctx, `UPDATE fundamental.provider_filing_link SET status='pending',filing_id=NULL`); e != nil {
		t.Fatal(e)
	}
	out, e := MaterializeCanonicalFundamentals(ctx, db, 4, "tdx")
	if e != nil || out.Removed != 0 || out.Updated != 0 {
		t.Fatal(out, e)
	}
	if _, e = RefreshProviderFilingLinks(ctx, db, 5, "tdx"); e != nil {
		t.Fatal(e)
	}
	if e = os.WriteFile(path, []byte("tampered"), 0600); e != nil {
		t.Fatal(e)
	}
	if _, e = MaterializeCanonicalFundamentals(ctx, db, 6, "tdx"); e == nil {
		t.Fatal("tampered source accepted")
	}
}

func TestMaterializeSingleQuarterFlowsAndRepairLegacyPeriods(t *testing.T) {
	db, _, _, _ := linkedFinancialFixture(t)
	ctx := t.Context()
	if _, e := MaterializeCanonicalFundamentals(ctx, db, 2, "tdx"); e != nil {
		t.Fatal(e)
	}
	var period string
	var value string
	e := db.QueryRowContext(ctx, `SELECT period_type,CAST(value AS VARCHAR) FROM fundamental.financial_observations(NULL,NULL,NULL,NULL) WHERE canonical_field='revenue'`).Scan(&period, &value)
	if e != nil || period != "Q4" || value != "100.0000000000" {
		t.Fatal(period, value, e)
	}
	e = db.QueryRowContext(ctx, `SELECT CAST(value AS VARCHAR) FROM fundamental.financial_observations(NULL,NULL,NULL,NULL) WHERE canonical_field='lease_liabilities'`).Scan(&value)
	if e != nil || value != "3097999.8779296875" {
		t.Fatal(value, e)
	}
}

func TestMaterializationRequiresCurrentStandardCatalogue(t *testing.T) {
	db, _, _, _ := linkedFinancialFixture(t)
	ctx := t.Context()
	if _, e := MaterializeCanonicalFundamentals(ctx, db, 2, "tdx"); e != nil {
		t.Fatal(e)
	}
	if _, e := db.ExecContext(ctx, `UPDATE fundamental.field SET unit='USD' WHERE canonical_field='revenue'`); e != nil {
		t.Fatal(e)
	}
	out, e := MaterializeCanonicalFundamentals(ctx, db, 3, "tdx")
	if e != nil || out.Removed != 1 {
		t.Fatal(out, e)
	}
	if _, e = db.ExecContext(ctx, `INSERT INTO fundamental.provider_field SELECT * REPLACE(DATE '2025-01-01' AS valid_from) FROM fundamental.provider_field WHERE canonical_field='revenue'`); e != nil {
		t.Fatal(e)
	}
	if _, e = MaterializeCanonicalFundamentals(ctx, db, 4, "tdx"); e == nil {
		t.Fatal("overlapping source mappings accepted")
	}
}

func assertAsOfRevenue(t *testing.T, ctx context.Context, db *sql.DB, instrumentID int64, period, asOf time.Time, want bool, wantValue float64) {
	t.Helper()
	var value float64
	e := db.QueryRowContext(ctx, `SELECT CAST(value AS DOUBLE) FROM fundamental.financial_observations_asof(NULL,NULL,NULL,?) WHERE instrument_id=? AND canonical_field='revenue' AND report_period=?`, asOf, instrumentID, period).Scan(&value)
	if !want {
		if e != sql.ErrNoRows {
			t.Fatal(e)
		}
		return
	}
	if e != nil || value != wantValue {
		t.Fatal(value, e)
	}
}

func TestMaterializationRollsBackAllPackagesAndCatalogue(t *testing.T) {
	db, first, _, _ := linkedFinancialFixture(t)
	ctx := t.Context()
	second, hash, path := archiveFinancialFixture(t, ctx, db, "002032", 2, first.ReportPeriod, map[int]float32{230: 200, 439: 400})
	if _, err := ReconcileFinancialSourceRecords(ctx, db, 1, "tdx", hash, []domain.ProviderFinancialRecord{second}); err != nil {
		t.Fatal(err)
	}
	if _, err := db.ExecContext(ctx, `INSERT INTO fundamental.filing(filing_id,instrument_id,source,source_filing_id,provider_code,report_period,announcement_time,filing_type,filing_variant,resolution_status) VALUES(2,2,'cninfo','second','002032','2025-12-31','2026-03-01','annual','full','resolved')`); err != nil {
		t.Fatal(err)
	}
	if _, err := RefreshProviderFilingLinks(ctx, db, 1, "tdx"); err != nil {
		t.Fatal(err)
	}
	if _, err := MaterializeCanonicalFundamentals(ctx, db, 2, "tdx"); err != nil {
		t.Fatal(err)
	}
	var before string
	if err := db.QueryRowContext(ctx, `SELECT CAST(to_json(list(s ORDER BY artifact_id)) AS VARCHAR) FROM fundamental.materialization_state s`).Scan(&before); err != nil {
		t.Fatal(err)
	}
	if _, err := db.ExecContext(ctx, `UPDATE fundamental.provider_field SET value_multiplier=-1 WHERE canonical_field='lease_liabilities'`); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("tampered second package"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := MaterializeCanonicalFundamentals(ctx, db, 3, "tdx"); err == nil {
		t.Fatal("tampered second package accepted")
	}
	var after string
	var count, mult int
	if err := db.QueryRowContext(ctx, `SELECT CAST(to_json(list(s ORDER BY artifact_id)) AS VARCHAR) FROM fundamental.materialization_state s`).Scan(&after); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.financial_observations(NULL,NULL,NULL,NULL)`).Scan(&count); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRowContext(ctx, `SELECT value_multiplier FROM fundamental.statement_field WHERE canonical_field='lease_liabilities'`).Scan(&mult); err != nil {
		t.Fatal(err)
	}
	if before != after || count != 4 || mult != 10000 {
		t.Fatalf("partial publication: signatures equal=%v cells=%d multiplier=%d", before == after, count, mult)
	}
}

func TestMaterializeRetagsOnlyUnaffectedPeriods(t *testing.T) {
	db, _, _, path := linkedFinancialFixture(t)
	ctx := t.Context()
	first, err := MaterializeCanonicalFundamentals(ctx, db, 2, "tdx")
	if err != nil {
		t.Fatal(err)
	}
	if err = os.Rename(path, path+".held"); err != nil {
		t.Fatal(err)
	}
	// Changing only a closed historical interval must not reopen a later archive.
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET notes='historical review' WHERE valid_to=DATE '2025-01-01'`)
	if err != nil {
		t.Fatal(err)
	}
	replay, err := MaterializeCanonicalFundamentals(ctx, db, 3, "tdx")
	if err != nil || replay.Inserted != 0 || replay.Updated != 0 || replay.Removed != 0 || replay.Materialized != first.Materialized {
		t.Fatal(replay, err)
	}
	var signature string
	if err = db.QueryRowContext(ctx, `SELECT input_signature FROM fundamental.materialization_state`).Scan(&signature); err != nil {
		t.Fatal(err)
	}
	var run int
	if err = db.QueryRowContext(ctx, `SELECT ingest_run_id FROM fundamental.statement_snapshot`).Scan(&run); err != nil || run != 2 {
		t.Fatal(run, err)
	}
	// A simultaneous disclosure correction still requires decoding. Failure must
	// preserve the last published catalogue and signature, not partially retag.
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET notes='second historical review' WHERE valid_to=DATE '2025-01-01'; UPDATE fundamental.filing SET announcement_time='2026-03-02'`)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = MaterializeCanonicalFundamentals(ctx, db, 4, "tdx"); err == nil {
		t.Fatal("changed filing skipped missing archive")
	}
	var after string
	if err = db.QueryRowContext(ctx, `SELECT input_signature FROM fundamental.materialization_state`).Scan(&after); err != nil || after != signature {
		t.Fatal("failure advanced signature", err)
	}
	var count int
	if err = db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.statement_field WHERE notes='second historical review'`).Scan(&count); err != nil || count != 0 {
		t.Fatal("failure published catalogue", count, err)
	}
	if err = os.Rename(path+".held", path); err != nil {
		t.Fatal(err)
	}
	restored, err := MaterializeCanonicalFundamentals(ctx, db, 5, "tdx")
	if err != nil || restored.Updated != 0 {
		t.Fatal(restored, err)
	}
	// Definition changes cannot be disguised as an out-of-period mapping edit.
	if err = os.Rename(path, path+".held"); err != nil {
		t.Fatal(err)
	}
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET notes='third historical review' WHERE valid_to=DATE '2025-01-01'; UPDATE fundamental.field SET unit='invalid' WHERE canonical_field='lease_liabilities'`)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = MaterializeCanonicalFundamentals(ctx, db, 6, "tdx"); err == nil {
		t.Fatal("changed definition skipped missing archive")
	}
}
