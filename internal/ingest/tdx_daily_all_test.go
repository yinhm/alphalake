package ingest

import (
	"context"
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
	if summary.RunID <= 0 || summary.Instruments != 3 || summary.Attempted != 2 || summary.Synced != 2 || summary.Skipped != 1 || summary.Bars != 3 || summary.Quarantined != 0 {
		t.Fatalf("summary = %#v", summary)
	}
	if got, ok := source.sinceCalls["sh600519"]; !ok || !got.Equal(boundary) {
		t.Fatalf("incremental boundary = %v/%v, want %v/true", got, ok, boundary)
	}
	if source.fullCalls["sh600519"] != 0 || source.fullCalls["sh510300"] != 1 {
		t.Fatalf("full calls = %#v", source.fullCalls)
	}

	var runStatus string
	if err := db.QueryRowContext(ctx, `SELECT status FROM meta.ingest_run WHERE ingest_run_id=?`, summary.RunID).Scan(&runStatus); err != nil {
		t.Fatal(err)
	}
	if runStatus != duckstore.IngestRunCompleted {
		t.Fatalf("run status = %q, want completed", runStatus)
	}
}

func TestSyncAllTDXDailyQuarantinesBadRowAndRetriesUntilCorrected(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "quarantine.duckdb"))
	if err != nil {
		t.Fatalf("OpenAndMigrate() error = %v", err)
	}
	defer db.Close()

	bad := observation(domain.InstrumentEquity, "XSHG", "Bad", "sh600001")
	good := observation(domain.InstrumentEquity, "XSHG", "Good", "sh600002")
	day1 := time.Date(2026, 9, 2, 0, 0, 0, 0, time.UTC)
	day2 := time.Date(2026, 9, 3, 0, 0, 0, 0, time.UTC)
	badBar := validBar(day1, 10)
	badBar.High = 9

	source := &fakeIncrementalTDXSource{
		observations: []domain.InstrumentObservation{bad, good},
		full: map[string][]domain.DailyBar{
			"sh600001": {badBar, validBar(day2, 11)},
			"sh600002": {validBar(day2, 20)},
		},
	}

	first, err := SyncAllTDXDaily(ctx, db, source)
	if err != nil {
		t.Fatalf("first sync error = %v; row validation should not fail the symbol batch", err)
	}
	if first.Synced != 2 || first.Bars != 2 || first.Quarantined != 1 || len(first.Failures) != 0 {
		t.Fatalf("first summary = %#v", first)
	}

	var badInstrumentID int64
	if err := db.QueryRowContext(ctx, `SELECT instrument_id FROM ref.instrument_identifier WHERE provider='tdx' AND identifier_value='sh600001'`).Scan(&badInstrumentID); err != nil {
		t.Fatal(err)
	}
	var badRows int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM market.ohlcv_daily WHERE instrument_id=?`, badInstrumentID).Scan(&badRows); err != nil {
		t.Fatal(err)
	}
	if badRows != 1 {
		t.Fatalf("bad symbol stored rows=%d, want only later valid row", badRows)
	}

	checkpoint, ok, err := duckstore.GetCheckpoint(ctx, db, "tdx", tdxDailyDataset, dailyRetryCheckpointKey(badInstrumentID))
	if err != nil || !ok || checkpoint != "2026-09-02" {
		t.Fatalf("retry checkpoint = %q/%v/%v", checkpoint, ok, err)
	}
	var firstStatus string
	if err := db.QueryRowContext(ctx, `SELECT status FROM meta.ingest_run WHERE ingest_run_id=?`, first.RunID).Scan(&firstStatus); err != nil {
		t.Fatal(err)
	}
	if firstStatus != duckstore.IngestRunPartial {
		t.Fatalf("first run status=%q, want partial", firstStatus)
	}

	// Upstream later fixes the quarantined row. Even though the latest stored
	// date is day2, the durable checkpoint must force a retry from day1.
	source.incremental = map[string][]domain.DailyBar{
		"sh600001": {validBar(day1, 10), validBar(day2, 11)},
		"sh600002": {validBar(day2, 20)},
	}
	second, err := SyncAllTDXDaily(ctx, db, source)
	if err != nil {
		t.Fatalf("second sync error = %v", err)
	}
	if got := source.sinceCalls["sh600001"]; !got.Equal(day1) {
		t.Fatalf("retry boundary=%v, want %v", got, day1)
	}
	if second.Quarantined != 0 {
		t.Fatalf("second summary=%#v", second)
	}
	if _, ok, err := duckstore.GetCheckpoint(ctx, db, "tdx", tdxDailyDataset, dailyRetryCheckpointKey(badInstrumentID)); err != nil || ok {
		t.Fatalf("checkpoint should clear after correction: ok=%v err=%v", ok, err)
	}
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM market.ohlcv_daily WHERE instrument_id=?`, badInstrumentID).Scan(&badRows); err != nil {
		t.Fatal(err)
	}
	if badRows != 2 {
		t.Fatalf("bad symbol rows after repair=%d, want 2", badRows)
	}
}
