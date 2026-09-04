package duckdb

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

func TestAdjustmentInputSignatureTracksContentNotIngestLineage(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "signature.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	instrumentID, err := UpsertInstrument(ctx, db,
		domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHG", Currency: "CNY", Name: "Test"},
		domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600001"},
	)
	if err != nil {
		t.Fatal(err)
	}
	day1 := time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC)
	day2 := day1.AddDate(0, 0, 1)
	bars := []domain.DailyBar{
		{InstrumentID: instrumentID, TradeDate: day1, Open: 10, High: 11, Low: 9, Close: 10, Volume: 1000, Amount: 10000, Source: "tdx"},
		{InstrumentID: instrumentID, TradeDate: day2, Open: 11, High: 12, Low: 10, Close: 11, Volume: 1200, Amount: 13200, Source: "tdx"},
	}

	dailyRun1, err := StartIngestRun(ctx, db, "tdx", "daily_ohlcv", nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := UpsertDailyBarsForRun(ctx, db, dailyRun1, bars); err != nil {
		t.Fatal(err)
	}
	actionRun1, err := StartIngestRun(ctx, db, "tdx", "corporate_action", nil)
	if err != nil {
		t.Fatal(err)
	}
	action := domain.CorporateAction{
		InstrumentID: instrumentID, ActionDate: day2, ActionType: "distribution", Source: "tdx", SourceCategory: 1,
		SourceRecordID: "stable-record", CashDividendPer10: 1, RawC1: 1,
	}
	if err := ReplaceCorporateActionSnapshotForRun(ctx, db, actionRun1, instrumentID, "tdx", []domain.CorporateAction{action}, nil); err != nil {
		t.Fatal(err)
	}

	first, hasDaily, err := AdjustmentInputSignature(ctx, db, instrumentID, "tdx")
	if err != nil || !hasDaily {
		t.Fatalf("first signature=%q/%v/%v", first, hasDaily, err)
	}

	// Replay exactly the same canonical content under new lineage IDs. Both the
	// daily upsert metadata and corporate_action sequence IDs change, but the
	// content signature must remain stable.
	dailyRun2, _ := StartIngestRun(ctx, db, "tdx", "daily_ohlcv", nil)
	if err := UpsertDailyBarsForRun(ctx, db, dailyRun2, bars); err != nil {
		t.Fatal(err)
	}
	actionRun2, _ := StartIngestRun(ctx, db, "tdx", "corporate_action", nil)
	if err := ReplaceCorporateActionSnapshotForRun(ctx, db, actionRun2, instrumentID, "tdx", []domain.CorporateAction{action}, nil); err != nil {
		t.Fatal(err)
	}
	second, _, err := AdjustmentInputSignature(ctx, db, instrumentID, "tdx")
	if err != nil {
		t.Fatal(err)
	}
	if second != first {
		t.Fatalf("identical replay changed signature:\nfirst=%s\nsecond=%s", first, second)
	}

	// A same-date boundary correction must dirty the input even though row count
	// and max(trade_date) are unchanged.
	corrected := append([]domain.DailyBar(nil), bars...)
	corrected[1].Close = 11.25
	corrected[1].High = 12.25
	corrected[1].Amount = 13500
	dailyRun3, _ := StartIngestRun(ctx, db, "tdx", "daily_ohlcv", nil)
	if err := UpsertDailyBarsForRun(ctx, db, dailyRun3, corrected); err != nil {
		t.Fatal(err)
	}
	third, _, err := AdjustmentInputSignature(ctx, db, instrumentID, "tdx")
	if err != nil {
		t.Fatal(err)
	}
	if third == second {
		t.Fatal("daily content correction did not change signature")
	}

	// GBBQ record identity contains the raw-content fingerprint; changing it must
	// dirty adjustments even when action count/date are unchanged.
	action.SourceRecordID = "corrected-record"
	action.RawC1 = 2
	action.CashDividendPer10 = 2
	actionRun3, _ := StartIngestRun(ctx, db, "tdx", "corporate_action", nil)
	if err := ReplaceCorporateActionSnapshotForRun(ctx, db, actionRun3, instrumentID, "tdx", []domain.CorporateAction{action}, nil); err != nil {
		t.Fatal(err)
	}
	fourth, _, err := AdjustmentInputSignature(ctx, db, instrumentID, "tdx")
	if err != nil {
		t.Fatal(err)
	}
	if fourth == third {
		t.Fatal("corporate-action content correction did not change signature")
	}
}
