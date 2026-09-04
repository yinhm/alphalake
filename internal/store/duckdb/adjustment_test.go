package duckdb

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

func TestAdjustmentStoreRoundTripAndReplace(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "adjustment.duckdb"))
	if err != nil {
		t.Fatalf("OpenAndMigrate() error = %v", err)
	}
	defer db.Close()

	instrumentID, err := UpsertInstrument(ctx, db,
		domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHG", Currency: "CNY", Name: "Test"},
		domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600001"},
	)
	if err != nil {
		t.Fatalf("UpsertInstrument() error = %v", err)
	}

	day1 := time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC)
	day2 := time.Date(2026, 6, 2, 0, 0, 0, 0, time.UTC)
	if err := UpsertDailyBars(ctx, db, []domain.DailyBar{
		{InstrumentID: instrumentID, TradeDate: day1, Open: 10, High: 11, Low: 9, Close: 10, Volume: 1000, Amount: 10000, Source: "tdx"},
		{InstrumentID: instrumentID, TradeDate: day2, Open: 9, High: 10, Low: 8, Close: 9, Volume: 1200, Amount: 10800, Source: "tdx"},
	}); err != nil {
		t.Fatalf("UpsertDailyBars() error = %v", err)
	}

	runID, err := StartIngestRun(ctx, db, "tdx", "corporate_action", nil)
	if err != nil {
		t.Fatalf("StartIngestRun() error = %v", err)
	}
	action := domain.CorporateAction{
		InstrumentID: instrumentID, ActionDate: day2, ActionType: "distribution", Source: "tdx", SourceCategory: 1,
		SourceRecordID: "sh600001:20260602:1", CashDividendPer10: 10, RawC1: 10,
	}
	if err := ReplaceCorporateActionSnapshotForRun(ctx, db, runID, instrumentID, "tdx", []domain.CorporateAction{action}, nil); err != nil {
		t.Fatalf("ReplaceCorporateActionSnapshotForRun() error = %v", err)
	}

	bars, err := LoadDailyBars(ctx, db, instrumentID, "tdx")
	if err != nil || len(bars) != 2 {
		t.Fatalf("LoadDailyBars() = %d rows, %v", len(bars), err)
	}
	actions, err := LoadCorporateActions(ctx, db, instrumentID, "tdx")
	if err != nil || len(actions) != 1 || actions[0].CashDividendPer10 != 10 {
		t.Fatalf("LoadCorporateActions() = %#v, %v", actions, err)
	}

	calcRun, err := StartIngestRun(ctx, db, "tdx", "adjustment_segment", nil)
	if err != nil {
		t.Fatalf("StartIngestRun(adjustment) error = %v", err)
	}
	method := "affine_gbbq_v1"
	segments := []domain.AdjustmentSegment{
		{InstrumentID: instrumentID, EffectiveFrom: day1, EffectiveTo: &day1, QFQMul: 1, QFQAdd: -1, HFQMul: 1, HFQAdd: 0, Method: method, Source: "tdx"},
		{InstrumentID: instrumentID, EffectiveFrom: day2, QFQMul: 1, QFQAdd: 0, HFQMul: 1, HFQAdd: 1, Method: method, Source: "tdx"},
	}
	if err := ReplaceAdjustmentSegmentsForRun(ctx, db, calcRun, instrumentID, method, "tdx", segments); err != nil {
		t.Fatalf("ReplaceAdjustmentSegmentsForRun() error = %v", err)
	}

	newRun, err := StartIngestRun(ctx, db, "tdx", "adjustment_segment", nil)
	if err != nil {
		t.Fatalf("StartIngestRun(replace) error = %v", err)
	}
	if err := ReplaceAdjustmentSegmentsForRun(ctx, db, newRun, instrumentID, method, "tdx", segments[1:]); err != nil {
		t.Fatalf("ReplaceAdjustmentSegmentsForRun(replace) error = %v", err)
	}

	var count int
	var storedRun int64
	if err := db.QueryRowContext(ctx, `
		SELECT count(*), max(ingest_run_id)
		FROM market.adjustment_segment
		WHERE instrument_id=? AND method=? AND source='tdx'
	`, instrumentID, method).Scan(&count, &storedRun); err != nil {
		t.Fatalf("query adjustment segments: %v", err)
	}
	if count != 1 || storedRun != newRun {
		t.Fatalf("adjustment rows/run = %d/%d, want 1/%d", count, storedRun, newRun)
	}
}
