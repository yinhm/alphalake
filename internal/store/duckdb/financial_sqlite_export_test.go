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
	run, err := StartIngestRun(ctx, db, "tdx", "daily_ohlcv", nil)
	if err != nil {
		t.Fatal(err)
	}
	if err = UpsertDailyBarsForRun(ctx, db, run, []domain.DailyBar{{InstrumentID: id, TradeDate: day, Open: 10, High: 11, Low: 9, Close: 10, Volume: 100, Amount: 1000, Source: "tdx"}}); err != nil {
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
	if read()["quote"].(map[string]any)["close"] != "10.000000" {
		t.Fatal("quote lost")
	}
	if _, err = db.ExecContext(ctx, `UPDATE meta.ingest_run SET started_at=? WHERE ingest_run_id=?`, day, run); err != nil {
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
