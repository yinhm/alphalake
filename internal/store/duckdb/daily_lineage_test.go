package duckdb

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

func TestUpsertDailyBarsForRunTracksLatestWriter(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "lineage.duckdb"))
	if err != nil {
		t.Fatalf("OpenAndMigrate() error = %v", err)
	}
	defer db.Close()

	instrumentID, err := UpsertInstrument(ctx, db,
		domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHG", Currency: "CNY", Name: "贵州茅台"},
		domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600519"},
	)
	if err != nil {
		t.Fatalf("UpsertInstrument() error = %v", err)
	}

	run1, err := StartIngestRun(ctx, db, "tdx", "daily_ohlcv", nil)
	if err != nil {
		t.Fatalf("StartIngestRun(run1) error = %v", err)
	}
	run2, err := StartIngestRun(ctx, db, "tdx", "daily_ohlcv", nil)
	if err != nil {
		t.Fatalf("StartIngestRun(run2) error = %v", err)
	}

	day := time.Date(2026, 9, 3, 0, 0, 0, 0, time.UTC)
	bar := domain.DailyBar{
		InstrumentID: instrumentID, TradeDate: day,
		Open: 10, High: 11, Low: 9, Close: 10,
		Volume: 1000, Amount: 10000, Source: "tdx",
	}
	if err := UpsertDailyBarsForRun(ctx, db, run1, []domain.DailyBar{bar}); err != nil {
		t.Fatalf("UpsertDailyBarsForRun(run1) error = %v", err)
	}
	bar.Close = 10.5
	if err := UpsertDailyBarsForRun(ctx, db, run2, []domain.DailyBar{bar}); err != nil {
		t.Fatalf("UpsertDailyBarsForRun(run2) error = %v", err)
	}

	var gotRun int64
	var gotClose float64
	if err := db.QueryRowContext(ctx, `
		SELECT ingest_run_id, close
		FROM market.ohlcv_daily
		WHERE instrument_id=? AND trade_date=? AND source='tdx'
	`, instrumentID, day).Scan(&gotRun, &gotClose); err != nil {
		t.Fatalf("query lineage: %v", err)
	}
	if gotRun != run2 || gotClose != bar.Close {
		t.Fatalf("lineage/close = %d/%.2f, want %d/%.2f", gotRun, gotClose, run2, bar.Close)
	}
}
