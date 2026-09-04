package ingest

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestAdjustmentDirtyStateSurvivesNormalSyncWorkflow(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "workflow.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	equity := observation(domain.InstrumentEquity, "XSHG", "Test", "sh600001")
	day1 := time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC)
	day2 := day1.AddDate(0, 0, 1)
	daily := &fakeIncrementalTDXSource{
		observations: []domain.InstrumentObservation{equity},
		full: map[string][]domain.DailyBar{
			"sh600001": {validBar(day1, 10), validBar(day2, 9)},
		},
		incremental: map[string][]domain.DailyBar{
			"sh600001": {validBar(day2, 9)},
		},
	}
	actionRow := actionObservation("sh600001", day2, 1, "distribution", false)
	actionRow.Action.SourceRecordID = "stable-action"
	actionRow.Action.CashDividendPer10 = 1
	actionRow.Action.RawC1 = 1
	actions := &fakeCorporateActionSource{
		instruments: []domain.InstrumentObservation{equity},
		actions: map[string][]domain.CorporateActionObservation{"sh600001": {actionRow}},
		errors: map[string]error{},
	}

	if _, err := SyncAllTDXDaily(ctx, db, daily); err != nil {
		t.Fatalf("first daily sync: %v", err)
	}
	if _, err := SyncTDXCorporateActions(ctx, db, actions); err != nil {
		t.Fatalf("first action sync: %v", err)
	}
	first, err := CalculateTDXAdjustments(ctx, db)
	if err != nil || first.Calculated != 1 {
		t.Fatalf("first adjustment = %#v, err=%v", first, err)
	}

	// Normal refresh re-fetches the boundary bar and fully replaces the same GBBQ
	// snapshot. Ingest lineage/sequence IDs change, canonical content does not.
	if _, err := SyncAllTDXDaily(ctx, db, daily); err != nil {
		t.Fatalf("second daily sync: %v", err)
	}
	if _, err := SyncTDXCorporateActions(ctx, db, actions); err != nil {
		t.Fatalf("second action sync: %v", err)
	}
	second, err := CalculateTDXAdjustments(ctx, db)
	if err != nil {
		t.Fatalf("second adjustment: %v", err)
	}
	if second.Calculated != 0 || second.Skipped == 0 {
		t.Fatalf("identical sync replay should remain clean: %#v", second)
	}

	// Correct the already-existing boundary day without adding a newer date.
	daily.incremental["sh600001"] = []domain.DailyBar{validBar(day2, 9.5)}
	if _, err := SyncAllTDXDaily(ctx, db, daily); err != nil {
		t.Fatalf("corrected daily sync: %v", err)
	}
	third, err := CalculateTDXAdjustments(ctx, db)
	if err != nil || third.Calculated != 1 {
		t.Fatalf("daily correction should dirty adjustments: %#v err=%v", third, err)
	}

	// Correct GBBQ content while keeping the same action date/count.
	correctedAction := actionRow
	correctedAction.Action.SourceRecordID = "corrected-action"
	correctedAction.Action.CashDividendPer10 = 2
	correctedAction.Action.RawC1 = 2
	actions.actions["sh600001"] = []domain.CorporateActionObservation{correctedAction}
	if _, err := SyncTDXCorporateActions(ctx, db, actions); err != nil {
		t.Fatalf("corrected action sync: %v", err)
	}
	fourth, err := CalculateTDXAdjustments(ctx, db)
	if err != nil || fourth.Calculated != 1 {
		t.Fatalf("action correction should dirty adjustments: %#v err=%v", fourth, err)
	}
}
