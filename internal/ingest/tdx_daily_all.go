package ingest

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

const tdxDailyDataset = "daily_ohlcv"

// TDXIncrementalDailySource extends the initial source boundary with an
// inclusive incremental fetch used by all-market synchronization.
type TDXIncrementalDailySource interface {
	TDXDailySource
	StockDailyBarsSince(context.Context, int64, string, time.Time) ([]domain.DailyBar, error)
}

type TDXDailySyncFailure struct {
	Symbol string
	Err    error
}

type TDXDailySyncSummary struct {
	RunID       int64
	Instruments int
	Attempted   int
	Synced      int
	Skipped     int
	Bars        int
	Quarantined int
	Failures    []TDXDailySyncFailure
}

type TDXDailyProgress struct {
	RunID       int64
	Processed   int
	Total       int
	Synced      int
	Failed      int
	Quarantined int
	Symbol      string
}

type TDXDailySyncOptions struct {
	OnProgress func(TDXDailyProgress)
}

type TDXDailyBatchError struct {
	Failures []TDXDailySyncFailure
}

func (e *TDXDailyBatchError) Error() string {
	if e == nil || len(e.Failures) == 0 {
		return ""
	}
	first := e.Failures[0]
	return fmt.Sprintf("%d TDX instruments failed; first %s: %v", len(e.Failures), first.Symbol, first.Err)
}

func SyncAllTDXDaily(ctx context.Context, db *sql.DB, source TDXIncrementalDailySource) (TDXDailySyncSummary, error) {
	return SyncAllTDXDailyWithOptions(ctx, db, source, TDXDailySyncOptions{})
}

// SyncAllTDXDailyWithOptions refreshes the TDX instrument master, then
// synchronizes canonical daily history for equities and ETFs. Structural bad
// rows are quarantined individually: valid rows still advance, while the
// earliest quarantined calendar day is retained as a durable retry checkpoint.
func SyncAllTDXDailyWithOptions(ctx context.Context, db *sql.DB, source TDXIncrementalDailySource, options TDXDailySyncOptions) (summary TDXDailySyncSummary, retErr error) {
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
		finalizeTrackedRun(ctx, db, runID, ingestRunStatus(summary, retErr), &retErr)
	}()

	observations, err := source.Instruments(ctx)
	if err != nil {
		return summary, fmt.Errorf("list TDX instruments: %w", err)
	}
	summary.Instruments = len(observations)
	eligibleTotal := countDailyEligible(observations)

	instrumentIDs, err := duckstore.UpsertInstruments(ctx, db, observations)
	if err != nil {
		return summary, fmt.Errorf("refresh canonical instrument master: %w", err)
	}

	for i, observation := range observations {
		if err := ctx.Err(); err != nil {
			return summary, err
		}
		if !equityOrETF(observation.Instrument.Type) {
			summary.Skipped++
			continue
		}

		summary.Attempted++
		symbol := observation.Identifier.Value
		instrumentID := instrumentIDs[i]
		boundary, hasBoundary, err := dailyFetchBoundary(ctx, db, observation.Identifier.Provider, instrumentID)
		if err != nil {
			summary.Failures = append(summary.Failures, TDXDailySyncFailure{Symbol: symbol, Err: err})
			reportTDXDailyProgress(options, summary, eligibleTotal, symbol)
			continue
		}

		var bars []domain.DailyBar
		if hasBoundary {
			bars, err = source.StockDailyBarsSince(ctx, instrumentID, symbol, boundary)
		} else {
			bars, err = source.StockDailyBars(ctx, instrumentID, symbol)
		}
		if err != nil {
			summary.Failures = append(summary.Failures, TDXDailySyncFailure{Symbol: symbol, Err: err})
			reportTDXDailyProgress(options, summary, eligibleTotal, symbol)
			continue
		}

		applied, err := applyDailyRows(ctx, db, runID, observation.Identifier.Provider, instrumentID, bars)
		if err != nil {
			summary.Failures = append(summary.Failures, TDXDailySyncFailure{Symbol: symbol, Err: err})
			reportTDXDailyProgress(options, summary, eligibleTotal, symbol)
			continue
		}
		summary.Synced++
		summary.Bars += applied.Written
		summary.Quarantined += applied.Quarantined
		reportTDXDailyProgress(options, summary, eligibleTotal, symbol)
	}

	if len(summary.Failures) != 0 {
		return summary, &TDXDailyBatchError{Failures: summary.Failures}
	}
	return summary, nil
}

func reportTDXDailyProgress(options TDXDailySyncOptions, summary TDXDailySyncSummary, total int, symbol string) {
	if options.OnProgress == nil {
		return
	}
	options.OnProgress(TDXDailyProgress{
		RunID: summary.RunID, Processed: summary.Attempted, Total: total,
		Synced: summary.Synced, Failed: len(summary.Failures), Quarantined: summary.Quarantined, Symbol: symbol,
	})
}

func countDailyEligible(observations []domain.InstrumentObservation) int {
	count := 0
	for _, observation := range observations {
		if equityOrETF(observation.Instrument.Type) {
			count++
		}
	}
	return count
}

func ingestRunStatus(summary TDXDailySyncSummary, runErr error) string {
	if errors.Is(runErr, context.Canceled) || errors.Is(runErr, context.DeadlineExceeded) {
		return duckstore.IngestRunCanceled
	}
	if runErr == nil && summary.Quarantined == 0 {
		return duckstore.IngestRunCompleted
	}
	if runErr == nil && summary.Quarantined > 0 {
		return duckstore.IngestRunPartial
	}
	if (len(summary.Failures) > 0 || summary.Quarantined > 0) && summary.Synced > 0 {
		return duckstore.IngestRunPartial
	}
	return duckstore.IngestRunFailed
}
