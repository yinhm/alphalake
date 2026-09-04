package ingest

import (
	"context"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

type fakeTDXDailySource struct {
	observations []domain.InstrumentObservation
	bars         []domain.DailyBar
	since        []domain.DailyBar
	sinceCalls   []time.Time
}

func (f *fakeTDXDailySource) Instruments(context.Context) ([]domain.InstrumentObservation, error) {
	return f.observations, nil
}

func (f *fakeTDXDailySource) StockDailyBars(_ context.Context, instrumentID int64, _ string) ([]domain.DailyBar, error) {
	return barsWithInstrumentID(f.bars, instrumentID), nil
}

func (f *fakeTDXDailySource) StockDailyBarsSince(_ context.Context, instrumentID int64, _ string, since time.Time) ([]domain.DailyBar, error) {
	f.sinceCalls = append(f.sinceCalls, since)
	return barsWithInstrumentID(f.since, instrumentID), nil
}

func TestSyncTDXDailyPersistsLineageAndThenUsesIncrementalBoundary(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "alphalake.duckdb"))
	if err != nil {
		t.Fatalf("OpenAndMigrate() error = %v", err)
	}
	defer db.Close()

	day1 := time.Date(2026, 9, 3, 0, 0, 0, 0, time.UTC)
	day2 := time.Date(2026, 9, 4, 0, 0, 0, 0, time.UTC)
	source := &fakeTDXDailySource{
		observations: []domain.InstrumentObservation{{
			Instrument: domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHG", Currency: "CNY", Name: "贵州茅台"},
			Identifier: domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600519"},
		}},
		bars: []domain.DailyBar{{
			TradeDate: day1, Open: 1500, High: 1520, Low: 1490, Close: 1510,
			Volume: 1234567, Amount: 1.86e9, Source: "tdx",
		}},
	}

	first, err := SyncTDXDailyWithSummary(ctx, db, source, "sh600519")
	if err != nil {
		t.Fatalf("first SyncTDXDailyWithSummary() error = %v", err)
	}
	if first.RunID <= 0 || first.Written != 1 || first.Quarantined != 0 {
		t.Fatalf("first summary = %#v", first)
	}

	var instrumentID, rowRunID int64
	if err := db.QueryRowContext(ctx, `
		SELECT i.instrument_id, d.ingest_run_id
		FROM ref.instrument_identifier i
		JOIN market.ohlcv_daily d USING (instrument_id)
		WHERE i.provider='tdx' AND i.identifier_value='sh600519' AND d.trade_date=?
	`, day1).Scan(&instrumentID, &rowRunID); err != nil {
		t.Fatalf("query first row lineage: %v", err)
	}
	if rowRunID != first.RunID {
		t.Fatalf("row ingest_run_id=%d, want %d", rowRunID, first.RunID)
	}

	source.since = []domain.DailyBar{
		{TradeDate: day1, Open: 1501, High: 1521, Low: 1491, Close: 1511, Volume: 123, Amount: 1, Source: "tdx"},
		{TradeDate: day2, Open: 1510, High: 1530, Low: 1500, Close: 1520, Volume: 456, Amount: 2, Source: "tdx"},
	}
	second, err := SyncTDXDailyWithSummary(ctx, db, source, "sh600519")
	if err != nil {
		t.Fatalf("second SyncTDXDailyWithSummary() error = %v", err)
	}
	if second.Written != 2 || len(source.sinceCalls) != 1 || !source.sinceCalls[0].Equal(day1) {
		t.Fatalf("second summary/calls = %#v / %#v", second, source.sinceCalls)
	}

	var count int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM market.ohlcv_daily WHERE instrument_id=?`, instrumentID).Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 2 {
		t.Fatalf("bar count=%d, want 2", count)
	}
}

func TestSyncTDXDailyRejectsUnsupportedInstrumentType(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "unsupported.duckdb"))
	if err != nil {
		t.Fatalf("OpenAndMigrate() error = %v", err)
	}
	defer db.Close()

	source := &fakeTDXDailySource{observations: []domain.InstrumentObservation{{
		Instrument: domain.InstrumentRef{Type: domain.InstrumentIndex, ExchangeMIC: "XSHG", Currency: "CNY", Name: "上证指数"},
		Identifier: domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh000001"},
	}}}

	_, err = SyncTDXDaily(ctx, db, source, "sh000001")
	if err == nil || !strings.Contains(err.Error(), "not supported") {
		t.Fatalf("SyncTDXDaily() error = %v, want unsupported type error", err)
	}

	var count int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM market.ohlcv_daily`).Scan(&count); err != nil {
		t.Fatalf("count bars: %v", err)
	}
	if count != 0 {
		t.Fatalf("bar count = %d, want 0", count)
	}
}
