package duckdb

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

func TestReplaceCorporateActionSnapshotForRunRemovesStaleEvents(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "actions.duckdb"))
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
	run1, err := StartIngestRun(ctx, db, "tdx", "corporate_actions", nil)
	if err != nil {
		t.Fatalf("StartIngestRun(run1) error = %v", err)
	}

	day1 := time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC)
	day2 := time.Date(2026, 7, 31, 0, 0, 0, 0, time.UTC)
	actions := []domain.CorporateAction{
		{InstrumentID: instrumentID, ActionDate: day1, ActionType: "distribution", Source: "tdx", SourceCategory: 1, SourceRecordID: "sh600519:2026-06-30:1", CashDividendPer10: 10, RawC1: 10},
		{InstrumentID: instrumentID, ActionDate: day2, ActionType: "share_capital_change", Source: "tdx", SourceCategory: 5, SourceRecordID: "sh600519:2026-07-31:5", RawC3: 123456789, RawC4: 200000000},
	}
	capital := []domain.ShareCapital{{
		InstrumentID: instrumentID, EffectiveDate: day2, FloatShares: 123456789, TotalShares: 200000000,
		Source: "tdx", SourceCategory: 5, SourceRecordID: "sh600519:2026-07-31:5",
	}}
	if err := ReplaceCorporateActionSnapshotForRun(ctx, db, run1, instrumentID, "tdx", actions, capital); err != nil {
		t.Fatalf("first ReplaceCorporateActionSnapshotForRun() error = %v", err)
	}

	run2, err := StartIngestRun(ctx, db, "tdx", "corporate_actions", nil)
	if err != nil {
		t.Fatalf("StartIngestRun(run2) error = %v", err)
	}
	replacement := []domain.CorporateAction{{
		InstrumentID: instrumentID, ActionDate: day1, ActionType: "scale", Source: "tdx", SourceCategory: 11,
		SourceRecordID: "sh600519:2026-06-30:11", ScaleFactor: 2, RawC3: 2,
	}}
	if err := ReplaceCorporateActionSnapshotForRun(ctx, db, run2, instrumentID, "tdx", replacement, nil); err != nil {
		t.Fatalf("second ReplaceCorporateActionSnapshotForRun() error = %v", err)
	}

	var actionCount, capitalCount int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM market.corporate_action WHERE instrument_id=? AND source='tdx'`, instrumentID).Scan(&actionCount); err != nil {
		t.Fatalf("count corporate actions: %v", err)
	}
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM market.share_capital WHERE instrument_id=? AND source='tdx'`, instrumentID).Scan(&capitalCount); err != nil {
		t.Fatalf("count share capital: %v", err)
	}
	if actionCount != 1 || capitalCount != 0 {
		t.Fatalf("snapshot counts = actions:%d capital:%d, want 1/0", actionCount, capitalCount)
	}

	var category int
	var scale float64
	var gotRun int64
	if err := db.QueryRowContext(ctx, `
		SELECT source_category, scale_factor, ingest_run_id
		FROM market.corporate_action
		WHERE instrument_id=? AND source='tdx'
	`, instrumentID).Scan(&category, &scale, &gotRun); err != nil {
		t.Fatalf("query replacement action: %v", err)
	}
	if category != 11 || scale != 2 || gotRun != run2 {
		t.Fatalf("replacement action = category:%d scale:%v run:%d", category, scale, gotRun)
	}
}
