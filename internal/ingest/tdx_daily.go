package ingest

import (
	"context"
	"database/sql"
	"errors"
	"fmt"

	"github.com/yinhm/alphalake/internal/domain"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

// TDXDailySource is the narrow source contract needed by daily market-data
// ingestion. The incremental extension is declared in tdx_daily_all.go.
type TDXDailySource interface {
	Instruments(context.Context) ([]domain.InstrumentObservation, error)
	StockDailyBars(context.Context, int64, string) ([]domain.DailyBar, error)
}

type TDXSingleDailySummary struct {
	RunID          int64
	Written        int
	Quarantined    int
	MasterFailures []InstrumentMasterFailure
}

// SyncTDXDaily remains as a compatibility wrapper. New callers that need run
// lineage/status should use SyncTDXDailyWithSummary.
func SyncTDXDaily(ctx context.Context, db *sql.DB, source TDXIncrementalDailySource, symbol string) (int, error) {
	summary, err := SyncTDXDailyWithSummary(ctx, db, source, symbol)
	return summary.Written, err
}

// SyncTDXDailyWithSummary resolves one TDX symbol into the canonical instrument
// master and performs the same resumable, quarantining, lineage-aware ingestion
// used by the all-market path.
func SyncTDXDailyWithSummary(ctx context.Context, db *sql.DB, source TDXIncrementalDailySource, symbol string) (summary TDXSingleDailySummary, retErr error) {
	if db == nil {
		return summary, fmt.Errorf("duckdb is nil")
	}
	if source == nil {
		return summary, fmt.Errorf("TDX source is nil")
	}

	runID, err := duckstore.StartIngestRun(ctx, db, "tdx", tdxDailyDataset, nil)
	if err != nil {
		return summary, fmt.Errorf("start TDX daily ingest run: %w", err)
	}
	summary.RunID = runID
	defer func() {
		finalizeTrackedRun(ctx, db, runID, singleDailyRunStatus(summary, retErr), &retErr)
	}()

	master, err := refreshInstrumentMaster(ctx, db, runID, source)
	if err != nil {
		return summary, fmt.Errorf("refresh TDX instrument master: %w", err)
	}
	summary.MasterFailures = master.Failures

	var observation *domain.InstrumentObservation
	var instrumentID int64
	for i := range master.Observations {
		if master.Observations[i].Identifier.Provider == "tdx" && master.Observations[i].Identifier.Value == symbol {
			observation = &master.Observations[i]
			instrumentID = master.InstrumentIDs[i]
			break
		}
	}
	if observation == nil {
		return summary, fmt.Errorf("TDX instrument %q not found", symbol)
	}
	if !equityOrETF(observation.Instrument.Type) {
		return summary, fmt.Errorf("TDX daily ingestion for %q type %q is not supported", symbol, observation.Instrument.Type)
	}

	boundary, hasBoundary, err := dailyFetchBoundary(ctx, db, observation.Identifier.Provider, instrumentID)
	if err != nil {
		return summary, err
	}
	var bars []domain.DailyBar
	if hasBoundary {
		bars, err = source.StockDailyBarsSince(ctx, instrumentID, symbol, boundary)
	} else {
		bars, err = source.StockDailyBars(ctx, instrumentID, symbol)
	}
	if err != nil {
		return summary, fmt.Errorf("fetch daily bars %q: %w", symbol, err)
	}

	applied, err := applyDailyRows(ctx, db, runID, observation.Identifier.Provider, instrumentID, bars)
	if err != nil {
		return summary, fmt.Errorf("store daily bars %q: %w", symbol, err)
	}
	summary.Written = applied.Written
	summary.Quarantined = applied.Quarantined
	return summary, nil
}

func singleDailyRunStatus(summary TDXSingleDailySummary, runErr error) string {
	if errors.Is(runErr, context.Canceled) || errors.Is(runErr, context.DeadlineExceeded) {
		return duckstore.IngestRunCanceled
	}
	if runErr != nil {
		return duckstore.IngestRunFailed
	}
	if summary.Quarantined > 0 || len(summary.MasterFailures) > 0 {
		return duckstore.IngestRunPartial
	}
	return duckstore.IngestRunCompleted
}
