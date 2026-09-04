package duckdb

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

func TestLatestDailyDateTracksNewestSourceObservation(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "checkpoint.duckdb"))
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

	if _, ok, err := LatestDailyDate(ctx, db, instrumentID, "tdx"); err != nil || ok {
		t.Fatalf("empty LatestDailyDate() ok/error = %v/%v, want false/nil", ok, err)
	}

	first := time.Date(2026, 9, 2, 0, 0, 0, 0, time.UTC)
	last := time.Date(2026, 9, 3, 0, 0, 0, 0, time.UTC)
	bars := []domain.DailyBar{
		{InstrumentID: instrumentID, TradeDate: last, Open: 10, High: 11, Low: 9, Close: 10, Source: "tdx"},
		{InstrumentID: instrumentID, TradeDate: first, Open: 9, High: 10, Low: 8, Close: 9, Source: "tdx"},
	}
	if err := UpsertDailyBars(ctx, db, bars); err != nil {
		t.Fatalf("UpsertDailyBars() error = %v", err)
	}

	got, ok, err := LatestDailyDate(ctx, db, instrumentID, "tdx")
	if err != nil {
		t.Fatalf("LatestDailyDate() error = %v", err)
	}
	if !ok || !got.Equal(last) {
		t.Fatalf("LatestDailyDate() = %v/%v, want %v/true", got, ok, last)
	}
}
