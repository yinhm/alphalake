package duckdb

import (
	"context"
	"encoding/json"
	"github.com/yinhm/alphalake/internal/domain"
	"math"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestFinancialSQLiteMarketQuoteBoundary(t *testing.T) {
	ctx := context.Background()
	db, err := OpenInitialized(ctx, filepath.Join(t.TempDir(), "db.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	id, err := UpsertInstrument(ctx, db, domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHE", Currency: "CNY"}, domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sz300866"})
	if err != nil {
		t.Fatal(err)
	}
	day := time.Date(2025, 12, 31, 0, 0, 0, 0, time.UTC)
	quoteDay := domain.CompletedMarketDate(time.Now()).AddDate(0, 0, -1)
	run, err := StartIngestRun(ctx, db, "tdx", "daily_ohlcv", nil)
	if err != nil {
		t.Fatal(err)
	}
	if err = UpsertDailyBarsForRun(ctx, db, run, []domain.DailyBar{{InstrumentID: id, TradeDate: quoteDay, Open: 10, High: 11, Low: 9, Close: 10, Volume: 100, Amount: 1000, Source: "tdx"}}); err != nil {
		t.Fatal(err)
	}
	if err = FinishIngestRun(ctx, db, run, IngestRunCompleted, nil, nil); err != nil {
		t.Fatal(err)
	}
	read := func() map[string]any {
		t.Helper()
		dir := filepath.Join(t.TempDir(), "rows")
		if err := ExportFinancialSQLiteRows(ctx, db, dir, []string{"300866"}, []string{"total_shares"}, day, day, time.Now()); err != nil {
			t.Fatal(err)
		}
		raw, err := os.ReadFile(filepath.Join(dir, "companies.jsonl"))
		if err != nil {
			t.Fatal(err)
		}
		var row map[string]any
		if err = json.Unmarshal(raw, &row); err != nil {
			t.Fatal(err)
		}
		return row
	}
	quote := read()["quote"].(map[string]any)
	if quote["close"] != "10.000000" || quote["trade_date"] != quoteDay.Format("2006-01-02") {
		t.Fatal("quote lost")
	}
	if _, err = db.ExecContext(ctx, `UPDATE meta.ingest_run SET started_at=? WHERE ingest_run_id=?`, quoteDay, run); err != nil {
		t.Fatal(err)
	}
	if read()["quote"] != nil {
		t.Fatal("intraday capture accepted")
	}
	var start string
	if err = db.QueryRowContext(ctx, `SELECT CAST(valid_from AS VARCHAR) FROM fundamental.provider_field WHERE canonical_field='research_and_development_expense'`).Scan(&start); err != nil || start != "1900-01-01" {
		t.Fatal("sample date still blocks historical RD", start, err)
	}
}

func TestHistoricalResearchExpenseNotLimitedByReviewDate(t *testing.T) {
	ctx := context.Background()
	db, err := OpenInitialized(ctx, filepath.Join(t.TempDir(), "rd.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	period := time.Date(2020, 12, 31, 0, 0, 0, 0, time.UTC)
	_, err = db.ExecContext(ctx, `INSERT INTO fundamental.filing(filing_id,instrument_id,source,source_filing_id,report_period,announcement_time,filing_type) VALUES(1,1,'cninfo','rd-history',?,?,'annual')`, period, period.AddDate(0, 3, 1))
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.ExecContext(ctx, `INSERT INTO fundamental.provider_filing_link(provider_source,provider_revision_key,provider_artifact_id,provider_code,report_period,instrument_id,filing_id,status,linker_version) VALUES('tdx','revision',1,'300866',?,1,1,'linked','test')`, period)
	if err != nil {
		t.Fatal(err)
	}
	fields, err := LoadSnapshotFields(ctx, db)
	if err != nil {
		t.Fatal(err)
	}
	r := domain.ProviderFinancialRecord{InstrumentID: 1, Provider: "tdx", ProviderCode: "300866", ReportPeriod: period, ProviderFields: make([]domain.ProviderFloat32, 584)}
	r.ProviderFields[303] = domain.ProviderFloat32{Bits: math.Float32bits(123.5), Value: 123.5}
	conn, err := db.Conn(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	apply := func(run int64) {
		t.Helper()
		_, e := MaterializeFinancialSnapshotBatch(ctx, conn, run, fields, []IndexedFinancialRecord{{ID: 1, Revision: "revision", Record: r}})
		if e != nil {
			t.Fatal(e)
		}
	}
	apply(1)
	var value float64
	if err = conn.QueryRowContext(ctx, `SELECT research_and_development_expense FROM fundamental.statement_snapshot`).Scan(&value); err != nil || value != 123.5 {
		t.Fatal(value, err)
	}
	if _, err = conn.ExecContext(ctx, `UPDATE fundamental.provider_field SET valid_from='2025-01-01' WHERE canonical_field='research_and_development_expense'`); err != nil {
		t.Fatal(err)
	}
	apply(2)
	var count int
	if err = conn.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.statement_snapshot WHERE research_and_development_expense IS NOT NULL`).Scan(&count); err != nil || count != 0 {
		t.Fatal("narrowed mapping retained unsupported value", count, err)
	}
}

// Current valuation consumes amounts even without a filing/date; historical
// date queries still exclude unknown dates. No CNINFO identity is fabricated.
func TestFinancialAvailabilityWithoutFiling(t *testing.T) {
	ctx := context.Background()
	db, err := OpenInitialized(ctx, filepath.Join(t.TempDir(), "db.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	id, err := UpsertInstrument(ctx, db, domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHE", Currency: "CNY"}, domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sz300866"})
	if err != nil {
		t.Fatal(err)
	}
	fields, err := LoadSnapshotFields(ctx, db)
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.Exec(`INSERT INTO meta.artifact(artifact_id,source,dataset,source_locator,fetched_at,sha256,content_length,local_path) VALUES(1,'tdx','professional_financial','fixture','2026-04-02','fixture',1,'fixture')`)
	if err != nil {
		t.Fatal(err)
	}
	conn, err := db.Conn(ctx)
	if err != nil {
		t.Fatal(err)
	}
	for i, year := range []int{2025, 2024, 2023} {
		period := time.Date(year, 12, 31, 0, 0, 0, 0, time.UTC)
		_, err = conn.ExecContext(ctx, `INSERT INTO fundamental.source_record VALUES(?,1,?,'300866',0,584,?,?)`, i+1, i+1, period, id)
		if err != nil {
			t.Fatal(err)
		}
		r := domain.ProviderFinancialRecord{InstrumentID: id, Provider: "tdx", ProviderCode: "300866", ReportPeriod: period, ProviderFields: make([]domain.ProviderFloat32, 584)}
		r.ProviderFields[229].Value = 123.5
		r.ProviderFields[313].Value = []float64{260401, 250230, 0}[i]
		records := []IndexedFinancialRecord{{ID: int64(i + 1), Revision: "fixture", Record: r}}
		if _, err = MaterializeFinancialSnapshotBatch(ctx, conn, 1, fields, records); err != nil {
			t.Fatal(err)
		}
		again, err := MaterializeFinancialSnapshotBatch(ctx, conn, 2, fields, records)
		if err != nil || again.Inserted+again.Updated+again.Removed != 0 {
			t.Fatal(again, err)
		}
	}
	conn.Close()
	var known, unknown, filings int
	if err = db.QueryRow(`SELECT count(*) FILTER(WHERE announcement_source='tdx' AND announcement_time='2026-04-01 16:00:00+00'),count(*) FILTER(WHERE announcement_source='unknown' AND announcement_time IS NULL),count(source_filing_id) FROM fundamental.statement_snapshot`).Scan(&known, &unknown, &filings); err != nil || known != 1 || unknown != 2 || filings != 0 {
		t.Fatal(known, unknown, filings, err)
	}

	// A different CNINFO date must not replace the valid primary TDX date.
	_, err = db.Exec(`INSERT INTO fundamental.filing(filing_id,instrument_id,source,source_filing_id,report_period,announcement_time,filing_type) VALUES(1,?,'cninfo','test','2025-12-31','2026-03-01','annual');`, id)
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.Exec(`INSERT INTO fundamental.provider_filing_link(provider_source,provider_revision_key,provider_artifact_id,provider_code,report_period,instrument_id,filing_id,status,linker_version) VALUES('tdx','fixture',1,'300866','2025-12-31',?,1,'linked','test')`, id)
	if err != nil {
		t.Fatal(err)
	}
	r := domain.ProviderFinancialRecord{InstrumentID: id, Provider: "tdx", ProviderCode: "300866", ReportPeriod: time.Date(2025, 12, 31, 0, 0, 0, 0, time.UTC), ProviderFields: make([]domain.ProviderFloat32, 584)}
	r.ProviderFields[229].Value = 123.5
	r.ProviderFields[313].Value = 260401
	conn, err = db.Conn(ctx)
	if err != nil {
		t.Fatal(err)
	}
	_, err = MaterializeFinancialSnapshotBatch(ctx, conn, 3, fields, []IndexedFinancialRecord{{ID: 1, Revision: "fixture", Record: r}})
	conn.Close()
	if err != nil {
		t.Fatal(err)
	}
	var n int
	if err = db.QueryRow(`SELECT count(*) FROM fundamental.financial_observations_asof('300866',NULL,NULL,'2026-04-01 15:59:59+00') WHERE canonical_field='revenue'`).Scan(&n); err != nil || n != 0 {
		t.Fatal(n, err)
	}
	if err = db.QueryRow(`SELECT count(*) FROM fundamental.financial_observations_asof('300866',NULL,NULL,'2026-04-01 16:00:00+00') WHERE canonical_field='revenue'`).Scan(&n); err != nil || n != 1 {
		t.Fatal(n, err)
	}
	dir := filepath.Join(t.TempDir(), "rows")
	if err = ExportFinancialSQLiteRows(ctx, db, dir, []string{"300866"}, []string{"revenue"}, time.Date(2023, 1, 1, 0, 0, 0, 0, time.UTC), time.Date(2025, 12, 31, 0, 0, 0, 0, time.UTC), time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)); err != nil {
		t.Fatal(err)
	}
	f, err := os.Open(filepath.Join(dir, "facts.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	decoder := json.NewDecoder(f)
	for i := 0; i < 3; i++ {
		var row map[string]any
		if err = decoder.Decode(&row); err != nil {
			t.Fatal(err)
		}
		if row["financial_time_basis"] != "current_standard_snapshot_not_pit" || row["value"] != "123.5000000000" {
			t.Fatal(row)
		}
	}
}

func TestCurrentFinancialExportDoesNotFillFromOlderRevision(t *testing.T) {
	ctx := context.Background()
	db, err := OpenInitialized(ctx, filepath.Join(t.TempDir(), "db.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	id, err := UpsertInstrument(ctx, db, domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHE", Currency: "CNY"}, domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sz300866"})
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.Exec(`INSERT INTO meta.artifact(artifact_id,source,dataset,source_locator,fetched_at,sha256,content_length,local_path) VALUES(1,'tdx','professional_financial','old','2026-04-01','old',1,'old'),(2,'tdx','professional_financial','new','2026-04-02','new',1,'new')`)
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.Exec(`INSERT INTO fundamental.source_record VALUES(1,1,1,'300866',0,584,'2025-12-31',?), (2,2,1,'300866',0,584,'2025-12-31',?)`, id, id)
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.Exec(`INSERT INTO fundamental.statement_snapshot(source_record_id,instrument_id,report_period,ingest_run_id,announcement_source,revenue) VALUES(1,?,'2025-12-31',1,'unknown',123)`, id)
	if err != nil {
		t.Fatal(err)
	}
	dir := filepath.Join(t.TempDir(), "rows")
	period := time.Date(2025, 12, 31, 0, 0, 0, 0, time.UTC)
	if err = ExportFinancialSQLiteRows(ctx, db, dir, []string{"300866"}, []string{"revenue"}, period, period, period.AddDate(0, 1, 0)); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(dir, "facts.jsonl"))
	if err != nil || len(raw) != 0 {
		t.Fatal("unavailable latest value silently replaced by old revision", string(raw), err)
	}
}
