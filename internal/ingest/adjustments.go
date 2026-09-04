package ingest

import (
	"context"
	"database/sql"
	"errors"
	"fmt"

	"github.com/yinhm/alphalake/internal/calc"
	"github.com/yinhm/alphalake/internal/domain"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

const tdxAdjustmentDataset = "adjustment_segment"

type AdjustmentFailure struct {
	Symbol string
	Err    error
}

type AdjustmentSummary struct {
	RunID       int64
	Instruments int
	Attempted   int
	Calculated  int
	Skipped     int
	Segments    int
	Failures    []AdjustmentFailure
}

type AdjustmentProgress struct {
	RunID      int64
	Processed  int
	Total      int
	Calculated int
	Failed     int
	Symbol     string
}

type AdjustmentOptions struct {
	OnProgress func(AdjustmentProgress)
}

type AdjustmentBatchError struct {
	Failures []AdjustmentFailure
}

func (e *AdjustmentBatchError) Error() string {
	if e == nil || len(e.Failures) == 0 {
		return ""
	}
	first := e.Failures[0]
	return fmt.Sprintf("%d adjustment instruments failed; first %s: %v", len(e.Failures), first.Symbol, first.Err)
}

func CalculateTDXAdjustments(ctx context.Context, db *sql.DB) (AdjustmentSummary, error) {
	return CalculateTDXAdjustmentsWithOptions(ctx, db, AdjustmentOptions{})
}

// CalculateTDXAdjustmentsWithOptions rebuilds adjustment segments strictly from
// canonical TDX daily bars and corporate actions already stored in DuckDB. It
// performs no network access.
func CalculateTDXAdjustmentsWithOptions(ctx context.Context, db *sql.DB, options AdjustmentOptions) (summary AdjustmentSummary, retErr error) {
	if db == nil {
		return summary, fmt.Errorf("duckdb is nil")
	}

	runID, err := duckstore.StartIngestRun(ctx, db, "tdx", tdxAdjustmentDataset, nil)
	if err != nil {
		return summary, fmt.Errorf("start adjustment calculation run: %w", err)
	}
	summary.RunID = runID
	defer func() {
		status := adjustmentRunStatus(summary, retErr)
		finishCtx := context.WithoutCancel(ctx)
		if err := duckstore.FinishIngestRun(finishCtx, db, runID, status, nil, retErr); err != nil {
			if retErr == nil {
				retErr = err
			} else {
				retErr = errors.Join(retErr, err)
			}
		}
	}()

	instruments, err := duckstore.ListProviderInstruments(ctx, db, "tdx")
	if err != nil {
		return summary, fmt.Errorf("list TDX instruments: %w", err)
	}
	summary.Instruments = len(instruments)
	eligibleTotal := countAdjustmentEligible(instruments)

	for _, instrument := range instruments {
		if err := ctx.Err(); err != nil {
			return summary, err
		}
		if !adjustmentEligible(instrument.Instrument.Type) {
			summary.Skipped++
			continue
		}

		summary.Attempted++
		instrumentID := instrument.Instrument.InstrumentID
		symbol := instrument.Identifier.Value
		bars, err := duckstore.LoadDailyBars(ctx, db, instrumentID, "tdx")
		if err != nil {
			summary.Failures = append(summary.Failures, AdjustmentFailure{Symbol: symbol, Err: err})
			reportAdjustmentProgress(options, summary, eligibleTotal, symbol)
			continue
		}
		if len(bars) == 0 {
			summary.Skipped++
			reportAdjustmentProgress(options, summary, eligibleTotal, symbol)
			continue
		}

		actions, err := duckstore.LoadCorporateActions(ctx, db, instrumentID, "tdx")
		if err != nil {
			summary.Failures = append(summary.Failures, AdjustmentFailure{Symbol: symbol, Err: err})
			reportAdjustmentProgress(options, summary, eligibleTotal, symbol)
			continue
		}
		segments, err := calc.AdjustmentSegments(bars, actions, calc.AdjustmentMethodAffineGBBQV1, "tdx")
		if err != nil {
			summary.Failures = append(summary.Failures, AdjustmentFailure{Symbol: symbol, Err: err})
			reportAdjustmentProgress(options, summary, eligibleTotal, symbol)
			continue
		}
		if err := duckstore.ReplaceAdjustmentSegmentsForRun(ctx, db, runID, instrumentID, calc.AdjustmentMethodAffineGBBQV1, "tdx", segments); err != nil {
			summary.Failures = append(summary.Failures, AdjustmentFailure{Symbol: symbol, Err: err})
			reportAdjustmentProgress(options, summary, eligibleTotal, symbol)
			continue
		}
		summary.Calculated++
		summary.Segments += len(segments)
		reportAdjustmentProgress(options, summary, eligibleTotal, symbol)
	}

	if len(summary.Failures) != 0 {
		return summary, &AdjustmentBatchError{Failures: summary.Failures}
	}
	return summary, nil
}

func adjustmentEligible(t domain.InstrumentType) bool {
	return t == domain.InstrumentEquity || t == domain.InstrumentETF
}

func countAdjustmentEligible(instruments []domain.InstrumentObservation) int {
	count := 0
	for _, instrument := range instruments {
		if adjustmentEligible(instrument.Instrument.Type) {
			count++
		}
	}
	return count
}

func reportAdjustmentProgress(options AdjustmentOptions, summary AdjustmentSummary, total int, symbol string) {
	if options.OnProgress == nil {
		return
	}
	options.OnProgress(AdjustmentProgress{
		RunID: summary.RunID,
		Processed: summary.Attempted,
		Total: total,
		Calculated: summary.Calculated,
		Failed: len(summary.Failures),
		Symbol: symbol,
	})
}

func adjustmentRunStatus(summary AdjustmentSummary, runErr error) string {
	if errors.Is(runErr, context.Canceled) || errors.Is(runErr, context.DeadlineExceeded) {
		return duckstore.IngestRunCanceled
	}
	if runErr == nil {
		return duckstore.IngestRunCompleted
	}
	if len(summary.Failures) > 0 && summary.Calculated > 0 {
		return duckstore.IngestRunPartial
	}
	return duckstore.IngestRunFailed
}
