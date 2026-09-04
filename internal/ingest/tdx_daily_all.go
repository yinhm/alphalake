package ingest

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
	"github.com/yinhm/alphalake/internal/validate"
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
	Failures    []TDXDailySyncFailure
}

type TDXDailyProgress struct {
	RunID     int64
	Processed int
	Total     int
	Synced    int
	Failed    int
	Symbol    string
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

// SyncAllTDXDaily refreshes the TDX instrument master and synchronizes daily
// history using default options.
func SyncAllTDXDaily(ctx context.Context, db *sql.DB, source TDXIncrementalDailySource) (TDXDailySyncSummary, error) {
	return SyncAllTDXDailyWithOptions(ctx, db, source, TDXDailySyncOptions{})
}

// SyncAllTDXDailyWithOptions refreshes the TDX instrument master, then
// synchronizes canonical daily history for equities and ETFs. Each instrument
// resumes from its own latest stored day; that boundary day is fetched again and
// upserted. Per-instrument failures are collected so one bad symbol does not
// discard the rest of the market update.
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
		status := ingestRunStatus(summary, retErr)
		finishCtx := context.WithoutCancel(ctx)
		if err := duckstore.FinishIngestRun(finishCtx, db, runID, status, nil, retErr); err != nil {
			if retErr == nil {
				retErr = err
			} else {
				retErr = errors.Join(retErr, err)
			}
		}
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
		if !dailyEligible(observation.Instrument.Type) {
			summary.Skipped++
			continue
		}

		summary.Attempted++
		symbol := observation.Identifier.Value
		instrumentID := instrumentIDs[i]
		latest, hasLatest, err := duckstore.LatestDailyDate(ctx, db, instrumentID, observation.Identifier.Provider)
		if err != nil {
			summary.Failures = append(summary.Failures, TDXDailySyncFailure{Symbol: symbol, Err: err})
			reportTDXDailyProgress(options, summary, eligibleTotal, symbol)
			continue
		}

		var bars []domain.DailyBar
		if hasLatest {
			bars, err = source.StockDailyBarsSince(ctx, instrumentID, symbol, latest)
		} else {
			bars, err = source.StockDailyBars(ctx, instrumentID, symbol)
		}
		if err != nil {
			summary.Failures = append(summary.Failures, TDXDailySyncFailure{Symbol: symbol, Err: err})
			reportTDXDailyProgress(options, summary, eligibleTotal, symbol)
			continue
		}

		if violations := validate.DailyBars(bars); len(violations) != 0 {
			validationErr := fmt.Errorf("daily validation failed: %s", summarizeViolations(violations))
			if err := duckstore.RecordValidationViolations(ctx, db, &runID, observation.Identifier.Provider, tdxDailyDataset, "daily_bar", violations); err != nil {
				validationErr = fmt.Errorf("%v; persist validation failures: %w", validationErr, err)
			}
			summary.Failures = append(summary.Failures, TDXDailySyncFailure{Symbol: symbol, Err: validationErr})
			reportTDXDailyProgress(options, summary, eligibleTotal, symbol)
			continue
		}
		if err := duckstore.UpsertDailyBarsForRun(ctx, db, runID, bars); err != nil {
			summary.Failures = append(summary.Failures, TDXDailySyncFailure{Symbol: symbol, Err: err})
			reportTDXDailyProgress(options, summary, eligibleTotal, symbol)
			continue
		}
		summary.Synced++
		summary.Bars += len(bars)
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
		Synced: summary.Synced, Failed: len(summary.Failures), Symbol: symbol,
	})
}

func countDailyEligible(observations []domain.InstrumentObservation) int {
	count := 0
	for _, observation := range observations {
		if dailyEligible(observation.Instrument.Type) {
			count++
		}
	}
	return count
}

func ingestRunStatus(summary TDXDailySyncSummary, runErr error) string {
	if errors.Is(runErr, context.Canceled) || errors.Is(runErr, context.DeadlineExceeded) {
		return duckstore.IngestRunCanceled
	}
	if runErr == nil {
		return duckstore.IngestRunCompleted
	}
	if len(summary.Failures) > 0 && summary.Synced > 0 {
		return duckstore.IngestRunPartial
	}
	return duckstore.IngestRunFailed
}

func dailyEligible(t domain.InstrumentType) bool {
	return t == domain.InstrumentEquity || t == domain.InstrumentETF
}

func summarizeViolations(violations []validate.Violation) string {
	const max = 3
	parts := make([]string, 0, minInt(len(violations), max))
	for i, violation := range violations {
		if i == max {
			break
		}
		parts = append(parts, violation.RuleCode+"("+violation.SubjectKey+")")
	}
	if len(violations) > max {
		parts = append(parts, fmt.Sprintf("+%d more", len(violations)-max))
	}
	return strings.Join(parts, ", ")
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}
