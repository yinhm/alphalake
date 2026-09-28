package duckdb

import (
	"database/sql"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	"github.com/yinhm/alphalake/internal/source/tdx/financial"
)

func TestCapitalHistoryReview(t *testing.T) {
	ctx := t.Context()
	db, err := OpenInitialized(ctx, filepath.Join(t.TempDir(), "history.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	check := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	var count int
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.provider_field WHERE notes LIKE 'official-capital-history-v1;%' AND valid_to=DATE '2025-01-01' AND zero_policy='reject'`).Scan(&count))
	if count != 19 {
		t.Fatal(count)
	}
	var before, after string
	original := `SELECT CAST(to_json(list(p ORDER BY provider_field)) AS VARCHAR) FROM fundamental.provider_field p WHERE valid_from=DATE '2025-01-01'`
	check(db.QueryRowContext(ctx, original).Scan(&before))
	_, err = db.ExecContext(ctx, `DELETE FROM fundamental.provider_field WHERE notes LIKE 'official-capital-history-v1;%'; DELETE FROM fundamental.statement_field WHERE notes LIKE 'official-capital-history-v1;%'`)
	check(err)
	n, err := ExtendCapitalHistory(ctx, db)
	check(err)
	if n != 19 {
		t.Fatal(n)
	}
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.statement_field WHERE notes LIKE 'official-capital-history-v1;%'`).Scan(&count))
	if count != 0 {
		t.Fatal("published semantics changed before atomic replay")
	}
	n, err = ExtendCapitalHistory(ctx, db)
	check(err)
	if n != 0 {
		t.Fatal("replay changed mappings", n)
	}
	check(db.QueryRowContext(ctx, original).Scan(&after))
	if before != after {
		t.Fatal("existing reviews changed")
	}
	// An incompatible historical review is never silently overwritten.
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET zero_policy='allow' WHERE canonical_field='lease_liabilities' AND valid_to=DATE '2025-01-01'`)
	check(err)
	if _, err = ExtendCapitalHistory(ctx, db); err == nil {
		t.Fatal("conflicting review accepted")
	}
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET zero_policy='reject' WHERE canonical_field='lease_liabilities' AND valid_to=DATE '2025-01-01'; UPDATE fundamental.source_field SET value_multiplier=1 WHERE name='lease_liabilities'`)
	check(err)
	if _, err = ExtendCapitalHistory(ctx, db); err == nil {
		t.Fatal("incompatible official unit accepted")
	}
}

// The archived record verifies the historical interval, scale and zero gate.
// It is a source replay, not an independent PDF or valuation classification.
func TestCapitalHistoryArchivedRecord(t *testing.T) {
	ctx := t.Context()
	raw, err := os.ReadFile("../../ingest/testdata/anker-cash-history-2024/gpcw20241231.zip")
	if err != nil {
		t.Fatal(err)
	}
	pkg, err := financial.ParsePackage("gpcw20241231.zip", raw)
	if err != nil {
		t.Fatal(err)
	}
	if len(pkg.Records) != 1 || pkg.Records[0].Code != "300866" {
		t.Fatal("unexpected fixture")
	}
	source := pkg.Records[0]
	db, err := OpenInitialized(ctx, filepath.Join(t.TempDir(), "archive.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	check := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	fields, err := LoadSnapshotFields(ctx, db)
	check(err)
	_, err = db.ExecContext(ctx, `INSERT INTO fundamental.filing(filing_id,instrument_id,source,source_filing_id,report_period,announcement_time,filing_type) VALUES(1,1,'cninfo','test',?,'2025-04-29T16:00:00Z','annual');`, source.ReportPeriod)
	check(err)
	_, err = db.ExecContext(ctx, `INSERT INTO fundamental.provider_filing_link(provider_source,provider_revision_key,provider_artifact_id,provider_code,report_period,instrument_id,filing_id,status,linker_version) VALUES('tdx','revision',1,'300866',?,1,1,'linked','test')`, source.ReportPeriod)
	check(err)
	conn, err := db.Conn(ctx)
	check(err)
	defer conn.Close()
	r := domain.ProviderFinancialRecord{InstrumentID: 1, Provider: "tdx", ProviderCode: source.Code, ReportPeriod: source.ReportPeriod, ProviderFields: source.Fields}
	_, err = conn.ExecContext(ctx, "BEGIN")
	check(err)
	_, err = MaterializeFinancialSnapshotBatch(ctx, conn, 1, fields, []IndexedFinancialRecord{{ID: 1, Revision: "revision", Record: r}})
	check(err)
	var lease, depreciation float64
	var bonds sql.NullFloat64
	check(conn.QueryRowContext(ctx, `SELECT lease_liabilities,depreciation_depletion,bonds_payable FROM fundamental.statement_snapshot`).Scan(&lease, &depreciation, &bonds))
	if lease != 62898500.9765625 || depreciation != 42345596 || bonds.Valid {
		t.Fatal(lease, depreciation, bonds)
	}
	// A corrupted multiplier must withdraw a previously supported value.
	_, err = conn.ExecContext(ctx, `UPDATE fundamental.provider_field SET value_multiplier=-1 WHERE canonical_field='lease_liabilities' AND valid_to=DATE '2025-01-01'`)
	check(err)
	_, err = MaterializeFinancialSnapshotBatch(ctx, conn, 2, fields, []IndexedFinancialRecord{{ID: 1, Revision: "revision", Record: r}})
	check(err)
	var rejected sql.NullFloat64
	check(conn.QueryRowContext(ctx, `SELECT lease_liabilities FROM fundamental.statement_snapshot`).Scan(&rejected))
	if rejected.Valid {
		t.Fatal("corrupt scale retained value")
	}
	_, err = conn.ExecContext(ctx, "ROLLBACK")
	check(err)
}

func TestCapitalHistoryCopyFailureIsRecorded(t *testing.T) {
	ctx := t.Context()
	db, err := OpenInitialized(ctx, filepath.Join(t.TempDir(), "copy.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	_, err = db.ExecContext(ctx, `DELETE FROM fundamental.provider_field WHERE notes LIKE 'official-capital-history-v1;%'; DELETE FROM fundamental.statement_field WHERE notes LIKE 'official-capital-history-v1;%'`)
	if err != nil {
		t.Fatal(err)
	}
	period := time.Date(2024, 12, 31, 0, 0, 0, 0, time.UTC)
	record, hash, path := archiveFinancialFixture(t, ctx, db, "300866", 1, period, map[int]float32{439: 310})
	if _, err = ReconcileFinancialSourceRecords(ctx, db, 1, "tdx", hash, []domain.ProviderFinancialRecord{record}); err != nil {
		t.Fatal(err)
	}
	if err = os.Rename(path, path+".held"); err != nil {
		t.Fatal(err)
	}
	run, _, err := RebuildCapitalHistoryCopy(ctx, db)
	if err == nil {
		t.Fatal("missing archive accepted")
	}
	var status, message string
	if err = db.QueryRowContext(ctx, `SELECT status,error_message FROM meta.ingest_run WHERE ingest_run_id=?`, run).Scan(&status, &message); err != nil || status != IngestRunFailed || message == "" {
		t.Fatal(status, message, err)
	}
	var count int
	if err = db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.statement_field WHERE notes LIKE 'official-capital-history-v1;%'`).Scan(&count); err != nil || count != 0 {
		t.Fatal("failed candidate published catalogue", count, err)
	}
}
