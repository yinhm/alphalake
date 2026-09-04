package ingest

import (
	"context"
	"errors"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/calc"
	"github.com/yinhm/alphalake/internal/domain"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestCalculateTDXAdjustmentsBuildsSegmentsAndSkipsCleanInputs(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "calc.duckdb"))
	if err != nil {
		t.Fatalf("OpenAndMigrate() error = %v", err)
	}
	defer db.Close()

	instrumentID, err := duckstore.UpsertInstrument(ctx, db,
		domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHG", Currency: "CNY", Name: "Test"},
		domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600001"},
	)
	if err != nil {
		t.Fatalf("UpsertInstrument() error = %v", err)
	}
	day1 := time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC)
	day2 := time.Date(2026, 6, 2, 0, 0, 0, 0, time.UTC)
	bars := []domain.DailyBar{
		{InstrumentID: instrumentID, TradeDate: day1, Open: 10, High: 11, Low: 9, Close: 10, Volume: 1000, Amount: 10000, Source: "tdx"},
		{InstrumentID: instrumentID, TradeDate: day2, Open: 9, High: 10, Low: 8, Close: 9, Volume: 1000, Amount: 9000, Source: "tdx"},
	}
	if err := duckstore.UpsertDailyBars(ctx, db, bars); err != nil {
		t.Fatalf("UpsertDailyBars() error = %v", err)
	}
	actionRun, err := duckstore.StartIngestRun(ctx, db, "tdx", "corporate_action", nil)
	if err != nil {
		t.Fatalf("StartIngestRun() error = %v", err)
	}
	action := domain.CorporateAction{
		InstrumentID: instrumentID, ActionDate: day2, ActionType: "distribution", Source: "tdx", SourceCategory: 1,
		SourceRecordID: "sh600001:20260602:1", CashDividendPer10: 10, RawC1: 10,
	}
	if err := duckstore.ReplaceCorporateActionSnapshotForRun(ctx, db, actionRun, instrumentID, "tdx", []domain.CorporateAction{action}, nil); err != nil {
		t.Fatalf("ReplaceCorporateActionSnapshotForRun() error = %v", err)
	}

	first, err := CalculateTDXAdjustments(ctx, db)
	if err != nil {
		t.Fatalf("first CalculateTDXAdjustments() error = %v", err)
	}
	if first.RunID <= 0 || first.Calculated != 1 || first.Segments != 2 {
		t.Fatalf("first summary = %#v", first)
	}

	var count int
	var oldAdd, newHFQAdd float64
	if err := db.QueryRowContext(ctx, `
		SELECT count(*), min(qfq_add), max(hfq_add)
		FROM market.adjustment_segment
		WHERE instrument_id=? AND method=? AND source='tdx' AND ingest_run_id=?
	`, instrumentID, calc.AdjustmentMethodAffineGBBQV1, first.RunID).Scan(&count, &oldAdd, &newHFQAdd); err != nil {
		t.Fatalf("query segments: %v", err)
	}
	if count != 2 || oldAdd != -1 || newHFQAdd != 1 {
		t.Fatalf("segments count/qfqAdd/hfqAdd = %d/%.3f/%.3f", count, oldAdd, newHFQAdd)
	}

	second, err := CalculateTDXAdjustments(ctx, db)
	if err != nil {
		t.Fatalf("second CalculateTDXAdjustments() error = %v", err)
	}
	if second.Calculated != 0 || second.Skipped == 0 {
		t.Fatalf("clean-input summary = %#v, want skipped without calculation", second)
	}
	var outputRun int64
	if err := db.QueryRowContext(ctx, `
		SELECT output_ingest_run_id FROM meta.derived_state
		WHERE dataset=? AND instrument_id=? AND source='tdx' AND method=?
	`, tdxAdjustmentDataset, instrumentID, calc.AdjustmentMethodAffineGBBQV1).Scan(&outputRun); err != nil {
		t.Fatalf("query derived state: %v", err)
	}
	if outputRun != first.RunID {
		t.Fatalf("clean rerun changed derived output run to %d, want %d", outputRun, first.RunID)
	}

	// Correct a historical input without adding a newer market date. Upsert updates
	// ingested_at, which must change the input signature and dirty the instrument.
	bars[0].Close = 10.5
	bars[0].High = 11.5
	if err := duckstore.UpsertDailyBars(ctx, db, []domain.DailyBar{bars[0]}); err != nil {
		t.Fatalf("historical UpsertDailyBars() error = %v", err)
	}
	third, err := CalculateTDXAdjustments(ctx, db)
	if err != nil {
		t.Fatalf("third CalculateTDXAdjustments() error = %v", err)
	}
	if third.Calculated != 1 {
		t.Fatalf("dirty-input summary = %#v, want recalculation", third)
	}
	if err := db.QueryRowContext(ctx, `
		SELECT output_ingest_run_id FROM meta.derived_state
		WHERE dataset=? AND instrument_id=? AND source='tdx' AND method=?
	`, tdxAdjustmentDataset, instrumentID, calc.AdjustmentMethodAffineGBBQV1).Scan(&outputRun); err != nil {
		t.Fatalf("query refreshed derived state: %v", err)
	}
	if outputRun != third.RunID {
		t.Fatalf("derived output run = %d, want %d", outputRun, third.RunID)
	}
}

func TestCalculateTDXAdjustmentsContinuesAfterInvalidInstrument(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "partial.duckdb"))
	if err != nil {
		t.Fatalf("OpenAndMigrate() error = %v", err)
	}
	defer db.Close()

	goodID, err := duckstore.UpsertInstrument(ctx, db,
		domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHG", Currency: "CNY", Name: "Good"},
		domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600001"},
	)
	if err != nil {
		t.Fatalf("UpsertInstrument(good) error = %v", err)
	}
	badID, err := duckstore.UpsertInstrument(ctx, db,
		domain.InstrumentRef{Type: domain.InstrumentETF, ExchangeMIC: "XSHG", Currency: "CNY", Name: "Bad"},
		domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh510001"},
	)
	if err != nil {
		t.Fatalf("UpsertInstrument(bad) error = %v", err)
	}
	day1 := time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC)
	day2 := time.Date(2026, 6, 2, 0, 0, 0, 0, time.UTC)
	for _, id := range []int64{goodID, badID} {
		if err := duckstore.UpsertDailyBars(ctx, db, []domain.DailyBar{
			{InstrumentID: id, TradeDate: day1, Open: 10, High: 11, Low: 9, Close: 10, Volume: 1000, Amount: 10000, Source: "tdx"},
			{InstrumentID: id, TradeDate: day2, Open: 10, High: 11, Low: 9, Close: 10, Volume: 1000, Amount: 10000, Source: "tdx"},
		}); err != nil {
			t.Fatalf("UpsertDailyBars(%d) error = %v", id, err)
		}
	}
	actionRun, err := duckstore.StartIngestRun(ctx, db, "tdx", "corporate_action", nil)
	if err != nil {
		t.Fatalf("StartIngestRun() error = %v", err)
	}
	badScale := domain.CorporateAction{
		InstrumentID: badID, ActionDate: day2, ActionType: "scale", Source: "tdx", SourceCategory: 11,
		SourceRecordID: "sh510001:20260602:11", ScaleFactor: -2, RawC3: -2,
	}
	if err := duckstore.ReplaceCorporateActionSnapshotForRun(ctx, db, actionRun, badID, "tdx", []domain.CorporateAction{badScale}, nil); err != nil {
		t.Fatalf("seed bad action: %v", err)
	}

	summary, err := CalculateTDXAdjustments(ctx, db)
	var batchErr *AdjustmentBatchError
	if !errors.As(err, &batchErr) {
		t.Fatalf("error = %v, want AdjustmentBatchError", err)
	}
	if summary.Calculated != 1 || len(summary.Failures) != 1 || summary.Failures[0].Symbol != "sh510001" {
		t.Fatalf("summary = %#v", summary)
	}
	var status string
	if err := db.QueryRowContext(ctx, `SELECT status FROM meta.ingest_run WHERE ingest_run_id=?`, summary.RunID).Scan(&status); err != nil {
		t.Fatalf("query run: %v", err)
	}
	if status != duckstore.IngestRunPartial {
		t.Fatalf("status = %q, want partial", status)
	}
}
