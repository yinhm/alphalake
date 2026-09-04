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
// performs no network access. Input lineage is summarized before historical data
// is loaded; unchanged instruments are skipped without a full-history scan.
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
		finalizeTrackedRun(ctx, db, runID, adjustmentRunStatus(summary, retErr), &retErr)
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
		if !equityOrETF(instrument.Instrument.Type) {
			summary.Skipped++
			continue
		}

		summary.Attempted++
		instrumentID := instrument.Instrument.InstrumentID
		symbol := instrument.Identifier.Value
		inputSignature, hasDaily, err := duckstore.AdjustmentInputSignature(ctx, db, instrumentID, "tdx")
		if err != nil {
			summary.Failures = append(summary.Failures, AdjustmentFailure{Symbol: symbol, Err: err})
			reportAdjustmentProgress(options, summary, eligibleTotal, symbol)
			continue
		}
		if !hasDaily {
			summary.Skipped++
			reportAdjustmentProgress(options, summary, eligibleTotal, symbol)
			continue
		}
		previousSignature, hasState, err := duckstore.DerivedStateSignature(
			ctx, db, tdxAdjustmentDataset, instrumentID, "tdx", calc.AdjustmentMethodAffineGBBQV1,
		)
		if err != nil {
			summary.Failures = append(summary.Failures, AdjustmentFailure{Symbol: symbol, Err: err})
			reportAdjustmentProgress(options, summary, eligibleTotal, symbol)
			continue
		}
		if hasState && previousSignature == inputSignature {
			summary.Skipped++
			reportAdjustmentProgress(options, summary, eligibleTotal, symbol)
			continue
		}

		bars, err := duckstore.LoadDailyBars(ctx, db, instrumentID, "tdx")
		if err != nil {
			summary.Failures = append(summary.Failures, AdjustmentFailure{Symbol: symbol, Err: err})
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
		if err := duckstore.ReplaceAdjustmentSegmentsAndStateForRun(
			ctx, db, runID, instrumentID, tdxAdjustmentDataset,
			calc.AdjustmentMethodAffineGBBQV1, "tdx", inputSignature, segments,
		); err != nil {
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

func countAdjustmentEligible(instruments []domain.InstrumentObservation) int {
	count := 0
	for _, instrument := range instruments {
		if equityOrETF(instrument.Instrument.Type) {
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
