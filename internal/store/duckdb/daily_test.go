package duckdb

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

func TestUpsertDailyBarsRefreshesExistingObservation(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "daily.duckdb"))
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

	date := time.Date(2026, 9, 3, 0, 0, 0, 0, time.UTC)
	bar := domain.DailyBar{
		InstrumentID: instrumentID,
		TradeDate:    date,
		Open:         1500.00,
		High:         1520.00,
		Low:          1490.00,
		Close:        1510.00,
		Volume:       1234567,
		Amount:       1.86e9,
		Source:       "tdx",
	}
	if err := UpsertDailyBars(ctx, db, []domain.DailyBar{bar}); err != nil {
		t.Fatalf("first UpsertDailyBars() error = %v", err)
	}

	bar.Close = 1512.34
	bar.Volume = 1234600
	if err := UpsertDailyBars(ctx, db, []domain.DailyBar{bar}); err != nil {
		t.Fatalf("second UpsertDailyBars() error = %v", err)
	}

	var count int
	var close float64
	var volume int64
	if err := db.QueryRowContext(ctx, `
		SELECT count(*), max(close), max(volume)
		FROM market.ohlcv_daily
		WHERE instrument_id = ? AND trade_date = ? AND source = 'tdx'
	`, instrumentID, date).Scan(&count, &close, &volume); err != nil {
		t.Fatalf("query daily bar: %v", err)
	}
	if count != 1 {
		t.Fatalf("row count = %d, want 1", count)
	}
	if close != bar.Close || volume != bar.Volume {
		t.Fatalf("stored close/volume = %.2f/%d, want %.2f/%d", close, volume, bar.Close, bar.Volume)
	}
}

func TestUpsertDailyBarsValidatesCanonicalKey(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "daily.duckdb"))
	if err != nil {
		t.Fatalf("OpenAndMigrate() error = %v", err)
	}
	defer db.Close()

	err = UpsertDailyBars(ctx, db, []domain.DailyBar{{Source: "tdx", TradeDate: time.Now()}})
	if err == nil {
		t.Fatal("UpsertDailyBars() expected validation error")
	}
}
