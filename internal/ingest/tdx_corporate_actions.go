package ingest

import (
	"context"
	"database/sql"
	"errors"
	"fmt"

	"github.com/yinhm/alphalake/internal/domain"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

const tdxCorporateActionDataset = "corporate_action"

type TDXCorporateActionSource interface {
	Instruments(context.Context) ([]domain.InstrumentObservation, error)
	CorporateActions(context.Context, string) ([]domain.CorporateActionObservation, error)
}

type TDXCorporateActionFailure struct {
	Symbol string
	Err    error
}

type TDXCorporateActionSummary struct {
	RunID       int64
	Instruments int
	Attempted   int
	Synced      int
	Skipped     int
	Actions     int
	ShareCapital int
	Failures    []TDXCorporateActionFailure
}

type TDXCorporateActionProgress struct {
	RunID     int64
	Processed int
	Total     int
	Synced    int
	Failed    int
	Symbol    string
}

type TDXCorporateActionSyncOptions struct {
	OnProgress func(TDXCorporateActionProgress)
}

type TDXCorporateActionBatchError struct {
	Failures []TDXCorporateActionFailure
}

func (e *TDXCorporateActionBatchError) Error() string {
	if e == nil || len(e.Failures) == 0 {
		return ""
	}
	first := e.Failures[0]
	return fmt.Sprintf("%d TDX corporate-action instruments failed; first %s: %v", len(e.Failures), first.Symbol, first.Err)
}

func SyncTDXCorporateActions(ctx context.Context, db *sql.DB, source TDXCorporateActionSource) (TDXCorporateActionSummary, error) {
	return SyncTDXCorporateActionsWithOptions(ctx, db, source, TDXCorporateActionSyncOptions{})
}

// SyncTDXCorporateActionsWithOptions refreshes TDX GBBQ snapshots for canonical
// equities and ETFs. Each successfully fetched symbol atomically replaces its
// prior provider snapshot, so upstream corrections/removals are reflected while
// a fetch failure cannot erase previously stored facts.
func SyncTDXCorporateActionsWithOptions(ctx context.Context, db *sql.DB, source TDXCorporateActionSource, options TDXCorporateActionSyncOptions) (summary TDXCorporateActionSummary, retErr error) {
	if db == nil {
		return summary, fmt.Errorf("duckdb is nil")
	}
	if source == nil {
		return summary, fmt.Errorf("TDX source is nil")
	}

	runID, err := duckstore.StartIngestRun(ctx, db, "tdx", tdxCorporateActionDataset, nil)
	if err != nil {
		return summary, fmt.Errorf("start TDX corporate-action ingest run: %w", err)
	}
	summary.RunID = runID
	defer func() {
		status := corporateActionRunStatus(summary, retErr)
		finishCtx := context.WithoutCancel(ctx)
		if err := duckstore.FinishIngestRun(finishCtx, db, runID, status, nil, retErr); err != nil {
			if retErr == nil {
				retErr = err
			} else {
				retErr = errors.Join(retErr, err)
			}
		}
	}()

	instruments, err := source.Instruments(ctx)
	if err != nil {
		return summary, fmt.Errorf("list TDX instruments: %w", err)
	}
	summary.Instruments = len(instruments)
	eligibleTotal := countCorporateActionEligible(instruments)

	instrumentIDs, err := duckstore.UpsertInstruments(ctx, db, instruments)
	if err != nil {
		return summary, fmt.Errorf("refresh canonical instrument master: %w", err)
	}

	for i, instrument := range instruments {
		if err := ctx.Err(); err != nil {
			return summary, err
		}
		if !corporateActionEligible(instrument.Instrument.Type) {
			summary.Skipped++
			continue
		}

		summary.Attempted++
		symbol := instrument.Identifier.Value
		instrumentID := instrumentIDs[i]
		observations, err := source.CorporateActions(ctx, symbol)
		if err != nil {
			summary.Failures = append(summary.Failures, TDXCorporateActionFailure{Symbol: symbol, Err: err})
			reportCorporateActionProgress(options, summary, eligibleTotal, symbol)
			continue
		}

		actions := make([]domain.CorporateAction, 0, len(observations))
		capital := make([]domain.ShareCapital, 0, len(observations))
		for _, observation := range observations {
			if observation.Identifier.Provider != instrument.Identifier.Provider || observation.Identifier.Value != symbol {
				err := fmt.Errorf("provider identifier mismatch: got %s/%s", observation.Identifier.Provider, observation.Identifier.Value)
				summary.Failures = append(summary.Failures, TDXCorporateActionFailure{Symbol: symbol, Err: err})
				actions = nil
				capital = nil
				break
			}
			action := observation.Action
			action.InstrumentID = instrumentID
			actions = append(actions, action)
			if observation.ShareCapital != nil {
				row := *observation.ShareCapital
				row.InstrumentID = instrumentID
				capital = append(capital, row)
			}
		}
		if actions == nil {
			reportCorporateActionProgress(options, summary, eligibleTotal, symbol)
			continue
		}

		if err := duckstore.ReplaceCorporateActionSnapshotForRun(ctx, db, runID, instrumentID, instrument.Identifier.Provider, actions, capital); err != nil {
			summary.Failures = append(summary.Failures, TDXCorporateActionFailure{Symbol: symbol, Err: err})
			reportCorporateActionProgress(options, summary, eligibleTotal, symbol)
			continue
		}
		summary.Synced++
		summary.Actions += len(actions)
		summary.ShareCapital += len(capital)
		reportCorporateActionProgress(options, summary, eligibleTotal, symbol)
	}

	if len(summary.Failures) != 0 {
		return summary, &TDXCorporateActionBatchError{Failures: summary.Failures}
	}
	return summary, nil
}

func corporateActionEligible(t domain.InstrumentType) bool {
	return t == domain.InstrumentEquity || t == domain.InstrumentETF
}

func countCorporateActionEligible(instruments []domain.InstrumentObservation) int {
	count := 0
	for _, instrument := range instruments {
		if corporateActionEligible(instrument.Instrument.Type) {
			count++
		}
	}
	return count
}

func reportCorporateActionProgress(options TDXCorporateActionSyncOptions, summary TDXCorporateActionSummary, total int, symbol string) {
	if options.OnProgress == nil {
		return
	}
	options.OnProgress(TDXCorporateActionProgress{
		RunID: summary.RunID,
		Processed: summary.Attempted,
		Total: total,
		Synced: summary.Synced,
		Failed: len(summary.Failures),
		Symbol: symbol,
	})
}

func corporateActionRunStatus(summary TDXCorporateActionSummary, runErr error) string {
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
