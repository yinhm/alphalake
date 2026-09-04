package ingest

import (
	"context"
	"errors"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

type fakeIncrementalTDXSource struct {
	observations []domain.InstrumentObservation
	full         map[string][]domain.DailyBar
	incremental  map[string][]domain.DailyBar
	fullCalls    map[string]int
	sinceCalls   map[string]time.Time
}

func (f *fakeIncrementalTDXSource) Instruments(context.Context) ([]domain.InstrumentObservation, error) {
	return f.observations, nil
}

func (f *fakeIncrementalTDXSource) StockDailyBars(_ context.Context, instrumentID int64, symbol string) ([]domain.DailyBar, error) {
	if f.fullCalls == nil {
		f.fullCalls = map[string]int{}
	}
	f.fullCalls[symbol]++
	return barsWithInstrumentID(f.full[symbol], instrumentID), nil
}

func (f *fakeIncrementalTDXSource) StockDailyBarsSince(_ context.Context, instrumentID int64, symbol string, since time.Time) ([]domain.DailyBar, error) {
	if f.sinceCalls == nil {
		f.sinceCalls = map[string]time.Time{}
	}
	f.sinceCalls[symbol] = since
	return barsWithInstrumentID(f.incremental[symbol], instrumentID), nil
}

func barsWithInstrumentID(in []domain.DailyBar, instrumentID int64) []domain.DailyBar {
	out := make([]domain.DailyBar, len(in))
	copy(out, in)
	for i := range out {
		out[i].InstrumentID = instrumentID
	}
	return out
}

func observation(t domain.InstrumentType, mic, name, symbol string) domain.InstrumentObservation {
	return domain.InstrumentObservation{
		Instrument: domain.InstrumentRef{Type: t, ExchangeMIC: mic, Currency: "CNY", Name: name},
		Identifier: domain.Identifier{Provider: "tdx", Type: "symbol", Value: symbol},
	}
}

func validBar(day time.Time, close float64) domain.DailyBar {
	return domain.DailyBar{
		TradeDate: day,
		Open: close - 0.1, High: close + 0.2, Low: close - 0.2, Close: close,
		Volume: 10000, Amount: close * 10000, Source: "tdx",
	}
}

func TestSyncAllTDXDailyUsesPerInstrumentBoundary(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "all.duckdb"))
	if err != nil {
		t.Fatalf("OpenAndMigrate() error = %v", err)
	}
	defer db.Close()

	equity := observation(domain.InstrumentEquity, "XSHG", "贵州茅台", "sh600519")
	etf := observation(domain.InstrumentETF, "XSHG", "沪深300ETF", "sh510300")
	bond := observation(domain.InstrumentBond, "XSHG", "转债样例", "sh113001")

	instrumentID, err := duckstore.UpsertInstrument(ctx, db, equity.Instrument, equity.Identifier)
	if err != nil {
		t.Fatalf("preload instrument: %v", err)
	}
	boundary := time.Date(2026, 9, 2, 0, 0, 0, 0, time.UTC)
	if err := duckstore.UpsertDailyBars(ctx, db, []domain.DailyBar{barsWithInstrumentID([]domain.DailyBar{validBar(boundary, 1500)}, instrumentID)[0]}); err != nil {
		t.Fatalf("preload daily bar: %v", err)
	}

	source := &fakeIncrementalTDXSource{
		observations: []domain.InstrumentObservation{equity, etf, bond},
		full: map[string][]domain.DailyBar{
			"sh510300": {validBar(time.Date(2026, 9, 3, 0, 0, 0, 0, time.UTC), 4.2)},
		},
		incremental: map[string][]domain.DailyBar{
			"sh600519": {
				validBar(boundary, 1501),
				validBar(time.Date(2026, 9, 3, 0, 0, 0, 0, time.UTC), 1510),
			},
		},
	}

	summary, err := SyncAllTDXDaily(ctx, db, source)
	if err != nil {
		t.Fatalf("SyncAllTDXDaily() error = %v", err)
	}
	if summary.RunID <= 0 || summary.Instruments != 3 || summary.Attempted != 2 || summary.Synced != 2 || summary.Skipped != 1 || summary.Bars != 3 {
		t.Fatalf("summary = %#v", summary)
	}
	if got, ok := source.sinceCalls["sh600519"]; !ok || !got.Equal(boundary) {
		t.Fatalf("incremental boundary = %v/%v, want %v/true", got, ok, boundary)
	}
	if source.fullCalls["sh600519"] != 0 || source.fullCalls["sh510300"] != 1 {
		t.Fatalf("full calls = %#v", source.fullCalls)
	}

	var equityRows, etfRows int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM market.ohlcv_daily WHERE instrument_id=?`, instrumentID).Scan(&equityRows); err != nil {
		t.Fatalf("count equity bars: %v", err)
	}
	if equityRows != 2 {
		t.Fatalf("equity rows = %d, want 2", equityRows)
	}
	if err := db.QueryRowContext(ctx, `
		SELECT count(*) FROM market.ohlcv_daily d
		JOIN ref.instrument_identifier i USING (instrument_id)
		WHERE i.provider='tdx' AND i.identifier_value='sh510300'
	`).Scan(&etfRows); err != nil {
		t.Fatalf("count ETF bars: %v", err)
	}
	if etfRows != 1 {
		t.Fatalf("ETF rows = %d, want 1", etfRows)
	}

	var runStatus string
	if err := db.QueryRowContext(ctx, `SELECT status FROM meta.ingest_run WHERE ingest_run_id=?`, summary.RunID).Scan(&runStatus); err != nil {
		t.Fatalf("query completed ingest run: %v", err)
	}
	if runStatus != duckstore.IngestRunCompleted {
		t.Fatalf("run status = %q, want %q", runStatus, duckstore.IngestRunCompleted)
	}
}

func TestSyncAllTDXDailyContinuesAfterInstrumentValidationFailure(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "partial.duckdb"))
	if err != nil {
		t.Fatalf("OpenAndMigrate() error = %v", err)
	}
	defer db.Close()

	bad := observation(domain.InstrumentEquity, "XSHG", "Bad", "sh600001")
	good := observation(domain.InstrumentEquity, "XSHG", "Good", "sh600002")
	day := time.Date(2026, 9, 3, 0, 0, 0, 0, time.UTC)
	badBar := validBar(day, 10)
	badBar.High = 9

	source := &fakeIncrementalTDXSource{
		observations: []domain.InstrumentObservation{bad, good},
		full: map[string][]domain.DailyBar{
			"sh600001": {badBar},
			"sh600002": {validBar(day, 20)},
		},
	}

	summary, err := SyncAllTDXDaily(ctx, db, source)
	var batchErr *TDXDailyBatchError
	if !errors.As(err, &batchErr) {
		t.Fatalf("error = %v, want TDXDailyBatchError", err)
	}
	if summary.RunID <= 0 || summary.Synced != 1 || len(summary.Failures) != 1 || summary.Failures[0].Symbol != "sh600001" {
		t.Fatalf("summary = %#v", summary)
	}

	var rows int
	if err := db.QueryRowContext(ctx, `
		SELECT count(*) FROM market.ohlcv_daily d
		JOIN ref.instrument_identifier i USING (instrument_id)
		WHERE i.identifier_value='sh600002'
	`).Scan(&rows); err != nil {
		t.Fatalf("count good bars: %v", err)
	}
	if rows != 1 {
		t.Fatalf("good rows = %d, want 1", rows)
	}

	var validations int
	if err := db.QueryRowContext(ctx, `
		SELECT count(*) FROM meta.validation_result
		WHERE source='tdx' AND dataset='daily_ohlcv' AND passed=false AND ingest_run_id=?
	`, summary.RunID).Scan(&validations); err != nil {
		t.Fatalf("count validation failures: %v", err)
	}
	if validations == 0 {
		t.Fatal("expected persisted validation failure linked to ingest run")
	}

	var runStatus string
	if err := db.QueryRowContext(ctx, `SELECT status FROM meta.ingest_run WHERE ingest_run_id=?`, summary.RunID).Scan(&runStatus); err != nil {
		t.Fatalf("query partial ingest run: %v", err)
	}
	if runStatus != duckstore.IngestRunPartial {
		t.Fatalf("run status = %q, want %q", runStatus, duckstore.IngestRunPartial)
	}
}
