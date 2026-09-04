package ingest

import (
	"context"
	"errors"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestCorporateActionForceReplaceCanClearLastGoodSnapshot(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "force-actions.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	equity := observation(domain.InstrumentEquity, "XSHG", "Test", "sh600001")
	instrumentID, err := duckstore.UpsertInstrument(ctx, db, equity.Instrument, equity.Identifier)
	if err != nil {
		t.Fatal(err)
	}
	seedRun, err := duckstore.StartIngestRun(ctx, db, "tdx", tdxCorporateActionDataset, nil)
	if err != nil {
		t.Fatal(err)
	}
	old := actionObservation("sh600001", time.Date(2025, 12, 31, 0, 0, 0, 0, time.UTC), 1, "distribution", false)
	old.Action.InstrumentID = instrumentID
	if err := duckstore.ReplaceCorporateActionSnapshotForRun(ctx, db, seedRun, instrumentID, "tdx", []domain.CorporateAction{old.Action}, nil); err != nil {
		t.Fatal(err)
	}

	source := &fakeCorporateActionSource{
		instruments: []domain.InstrumentObservation{equity},
		actions:     map[string][]domain.CorporateActionObservation{"sh600001": {}},
		errors:      map[string]error{},
	}
	defaultSummary, err := SyncTDXCorporateActions(ctx, db, source)
	var batchErr *TDXCorporateActionBatchError
	if !errors.As(err, &batchErr) || len(defaultSummary.Failures) != 1 {
		t.Fatalf("default sync error/summary = %v / %#v, want guarded failure", err, defaultSummary)
	}
	var count int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM market.corporate_action WHERE instrument_id=?`, instrumentID).Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 1 {
		t.Fatalf("default guard left %d rows, want last-good row preserved", count)
	}

	forced, err := SyncTDXCorporateActionsWithOptions(ctx, db, source, TDXCorporateActionSyncOptions{ForceReplace: true})
	if err != nil {
		t.Fatalf("forced sync: %v", err)
	}
	if forced.Synced != 1 || len(forced.Failures) != 0 {
		t.Fatalf("forced summary=%#v", forced)
	}
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM market.corporate_action WHERE instrument_id=?`, instrumentID).Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 0 {
		t.Fatalf("forced repair left %d rows, want explicit empty snapshot", count)
	}
}
