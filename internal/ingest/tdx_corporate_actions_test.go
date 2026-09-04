package ingest

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

type fakeCorporateActionSource struct {
	instruments []domain.InstrumentObservation
	actions     map[string][]domain.CorporateActionObservation
	errors      map[string]error
}

func (f *fakeCorporateActionSource) Instruments(context.Context) ([]domain.InstrumentObservation, error) {
	return f.instruments, nil
}

func (f *fakeCorporateActionSource) CorporateActions(_ context.Context, symbol string) ([]domain.CorporateActionObservation, error) {
	if err := f.errors[symbol]; err != nil {
		return nil, err
	}
	return f.actions[symbol], nil
}

func actionObservation(symbol string, day time.Time, category int, actionType string, capital bool) domain.CorporateActionObservation {
	recordID := fmt.Sprintf("%s:%s:%d", symbol, day.Format("20060102"), category)
	observation := domain.CorporateActionObservation{
		Identifier: domain.Identifier{Provider: "tdx", Type: "symbol", Value: symbol},
		Action: domain.CorporateAction{
			ActionDate: day, ActionType: actionType, Source: "tdx", SourceCategory: category,
			SourceRecordID: recordID,
		},
	}
	if capital {
		observation.ShareCapital = &domain.ShareCapital{
			EffectiveDate: day, FloatShares: 1000000, TotalShares: 2000000,
			Source: "tdx", SourceCategory: category, SourceRecordID: recordID,
		}
	}
	return observation
}

func TestSyncTDXCorporateActionsPersistsRunLineage(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "actions.duckdb"))
	if err != nil {
		t.Fatalf("OpenAndMigrate() error = %v", err)
	}
	defer db.Close()

	equity := observation(domain.InstrumentEquity, "XSHG", "贵州茅台", "sh600519")
	etf := observation(domain.InstrumentETF, "XSHG", "ETF", "sh510300")
	bond := observation(domain.InstrumentBond, "XSHG", "Bond", "sh113001")
	day := time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC)
	source := &fakeCorporateActionSource{
		instruments: []domain.InstrumentObservation{equity, etf, bond},
		actions: map[string][]domain.CorporateActionObservation{
			"sh600519": {actionObservation("sh600519", day, 5, "share_capital_change", true)},
			"sh510300": {actionObservation("sh510300", day, 11, "scale", false)},
		},
		errors: map[string]error{},
	}

	summary, err := SyncTDXCorporateActions(ctx, db, source)
	if err != nil {
		t.Fatalf("SyncTDXCorporateActions() error = %v", err)
	}
	if summary.RunID <= 0 || summary.Attempted != 2 || summary.Synced != 2 || summary.Skipped != 1 || summary.Actions != 2 || summary.ShareCapital != 1 {
		t.Fatalf("summary = %#v", summary)
	}

	var status string
	if err := db.QueryRowContext(ctx, `SELECT status FROM meta.ingest_run WHERE ingest_run_id=?`, summary.RunID).Scan(&status); err != nil {
		t.Fatalf("query ingest run: %v", err)
	}
	if status != duckstore.IngestRunCompleted {
		t.Fatalf("run status = %q, want %q", status, duckstore.IngestRunCompleted)
	}
}

func TestSyncTDXCorporateActionsKeepsFailedSymbolSnapshot(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "partial.duckdb"))
	if err != nil {
		t.Fatalf("OpenAndMigrate() error = %v", err)
	}
	defer db.Close()

	bad := observation(domain.InstrumentEquity, "XSHG", "Bad", "sh600001")
	good := observation(domain.InstrumentEquity, "XSHG", "Good", "sh600002")
	ids, err := duckstore.UpsertInstruments(ctx, db, []domain.InstrumentObservation{bad, good})
	if err != nil {
		t.Fatalf("UpsertInstruments() error = %v", err)
	}
	seedRun, err := duckstore.StartIngestRun(ctx, db, "tdx", tdxCorporateActionDataset, nil)
	if err != nil {
		t.Fatalf("StartIngestRun() error = %v", err)
	}
	oldDay := time.Date(2025, 12, 31, 0, 0, 0, 0, time.UTC)
	old := actionObservation("sh600001", oldDay, 1, "distribution", false)
	old.Action.InstrumentID = ids[0]
	if err := duckstore.ReplaceCorporateActionSnapshotForRun(ctx, db, seedRun, ids[0], "tdx", []domain.CorporateAction{old.Action}, nil); err != nil {
		t.Fatalf("seed snapshot: %v", err)
	}

	newDay := time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC)
	source := &fakeCorporateActionSource{
		instruments: []domain.InstrumentObservation{bad, good},
		actions: map[string][]domain.CorporateActionObservation{
			"sh600002": {actionObservation("sh600002", newDay, 1, "distribution", false)},
		},
		errors: map[string]error{"sh600001": errors.New("TDX unavailable")},
	}

	summary, err := SyncTDXCorporateActions(ctx, db, source)
	var batchErr *TDXCorporateActionBatchError
	if !errors.As(err, &batchErr) {
		t.Fatalf("error = %v, want TDXCorporateActionBatchError", err)
	}
	if summary.Synced != 1 || len(summary.Failures) != 1 || summary.Failures[0].Symbol != "sh600001" {
		t.Fatalf("summary = %#v", summary)
	}

	var badRows, goodRows int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM market.corporate_action WHERE instrument_id=? AND source='tdx'`, ids[0]).Scan(&badRows); err != nil {
		t.Fatalf("count bad snapshot: %v", err)
	}
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM market.corporate_action WHERE instrument_id=? AND source='tdx'`, ids[1]).Scan(&goodRows); err != nil {
		t.Fatalf("count good snapshot: %v", err)
	}
	if badRows != 1 || goodRows != 1 {
		t.Fatalf("snapshot rows bad/good = %d/%d, want 1/1", badRows, goodRows)
	}
}

func TestSyncTDXCorporateActionsRejectsEmptyResponseWhenHistoryExists(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "empty-guard.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	instrument := observation(domain.InstrumentEquity, "XSHG", "Existing", "sh600003")
	ids, err := duckstore.UpsertInstruments(ctx, db, []domain.InstrumentObservation{instrument})
	if err != nil {
		t.Fatal(err)
	}
	seedRun, err := duckstore.StartIngestRun(ctx, db, "tdx", tdxCorporateActionDataset, nil)
	if err != nil {
		t.Fatal(err)
	}
	old := actionObservation("sh600003", time.Date(2025, 6, 30, 0, 0, 0, 0, time.UTC), 1, "distribution", false)
	old.Action.InstrumentID = ids[0]
	if err := duckstore.ReplaceCorporateActionSnapshotForRun(ctx, db, seedRun, ids[0], "tdx", []domain.CorporateAction{old.Action}, nil); err != nil {
		t.Fatal(err)
	}

	source := &fakeCorporateActionSource{
		instruments: []domain.InstrumentObservation{instrument},
		actions:     map[string][]domain.CorporateActionObservation{"sh600003": {}},
		errors:      map[string]error{},
	}
	summary, err := SyncTDXCorporateActions(ctx, db, source)
	var batchErr *TDXCorporateActionBatchError
	if !errors.As(err, &batchErr) || len(summary.Failures) != 1 {
		t.Fatalf("summary/error = %#v / %v", summary, err)
	}
	var rows int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM market.corporate_action WHERE instrument_id=? AND source='tdx'`, ids[0]).Scan(&rows); err != nil {
		t.Fatal(err)
	}
	if rows != 1 {
		t.Fatalf("last good snapshot was erased; rows=%d", rows)
	}
}
