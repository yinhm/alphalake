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
	RunID          int64
	Instruments    int
	Attempted      int
	Synced         int
	Skipped        int
	Actions        int
	ShareCapital   int
	Failures       []TDXCorporateActionFailure
	MasterFailures []InstrumentMasterFailure
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
	OnProgress   func(TDXCorporateActionProgress)
	ForceReplace bool
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
// prior provider snapshot. By default, the new snapshot is compared with the
// last known-good one so a provider-side empty/truncated response cannot erase
// history. ForceReplace is an explicit repair escape hatch for a successfully
// fetched snapshot; it never bypasses fetch, identity, or database errors.
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
		finalizeTrackedRun(ctx, db, runID, corporateActionRunStatus(summary, retErr), &retErr)
	}()

	master, err := refreshInstrumentMaster(ctx, db, runID, source)
	if err != nil {
		return summary, fmt.Errorf("refresh TDX instrument master: %w", err)
	}
	instruments := master.Observations
	instrumentIDs := master.InstrumentIDs
	summary.MasterFailures = master.Failures
	summary.Instruments = len(instruments)
	eligibleTotal := countCorporateActionEligible(instruments)

	for i, instrument := range instruments {
		if err := ctx.Err(); err != nil {
			return summary, err
		}
		if !equityOrETF(instrument.Instrument.Type) {
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

		if !options.ForceReplace {
			previous, err := duckstore.GetCorporateActionSnapshotStats(ctx, db, instrumentID, instrument.Identifier.Provider)
			if err != nil {
				summary.Failures = append(summary.Failures, TDXCorporateActionFailure{Symbol: symbol, Err: err})
				reportCorporateActionProgress(options, summary, eligibleTotal, symbol)
				continue
			}
			if err := validateCorporateActionSnapshotReplacement(previous, len(actions), len(capital)); err != nil {
				summary.Failures = append(summary.Failures, TDXCorporateActionFailure{Symbol: symbol, Err: err})
				reportCorporateActionProgress(options, summary, eligibleTotal, symbol)
				continue
			}
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

func validateCorporateActionSnapshotReplacement(previous duckstore.CorporateActionSnapshotStats, actions, capital int) error {
	if previous.Actions > 0 && actions == 0 {
		return fmt.Errorf("refuse suspicious empty GBBQ snapshot: previous actions=%d", previous.Actions)
	}
	if previous.ShareCapital > 0 && capital == 0 {
		return fmt.Errorf("refuse GBBQ snapshot that drops all share-capital history: previous rows=%d", previous.ShareCapital)
	}
	if previous.Actions >= 10 && actions*2 < previous.Actions && previous.Actions-actions >= 5 {
		return fmt.Errorf("refuse suspiciously truncated GBBQ snapshot: actions %d -> %d", previous.Actions, actions)
	}
	if previous.ShareCapital >= 10 && capital*2 < previous.ShareCapital && previous.ShareCapital-capital >= 5 {
		return fmt.Errorf("refuse suspiciously truncated share-capital snapshot: rows %d -> %d", previous.ShareCapital, capital)
	}
	return nil
}

func countCorporateActionEligible(instruments []domain.InstrumentObservation) int {
	count := 0
	for _, instrument := range instruments {
		if equityOrETF(instrument.Instrument.Type) {
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
		Failed: len(summary.Failures) + len(summary.MasterFailures),
		Symbol: symbol,
	})
}

func corporateActionRunStatus(summary TDXCorporateActionSummary, runErr error) string {
	if errors.Is(runErr, context.Canceled) || errors.Is(runErr, context.DeadlineExceeded) {
		return duckstore.IngestRunCanceled
	}
	if runErr == nil {
		if len(summary.MasterFailures) > 0 {
			return duckstore.IngestRunPartial
		}
		return duckstore.IngestRunCompleted
	}
	if (len(summary.Failures) > 0 || len(summary.MasterFailures) > 0) && summary.Synced > 0 {
		return duckstore.IngestRunPartial
	}
	return duckstore.IngestRunFailed
}
