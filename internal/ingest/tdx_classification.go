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

const tdxClassificationDataset = "classification"

var chinaMarketZone = time.FixedZone("Asia/Shanghai", 8*60*60)

type TDXClassificationSource interface {
	Instruments(context.Context) ([]domain.InstrumentObservation, error)
	ClassificationFamilies() []string
	ClassificationSnapshot(context.Context, string) (domain.ClassificationSnapshot, error)
}

type TDXClassificationFailure struct {
	Family string
	Err    error
}

type TDXClassificationSummary struct {
	RunID    int64
	Families int
	Synced   int
	Nodes    int
	Members  int
	Opened   int
	Closed   int
	Failures []TDXClassificationFailure
}

type TDXClassificationProgress struct {
	RunID     int64
	Processed int
	Total     int
	Synced    int
	Failed    int
	Family    string
}

type TDXClassificationSyncOptions struct {
	Now        func() time.Time
	OnProgress func(TDXClassificationProgress)
}

type TDXClassificationBatchError struct {
	Failures []TDXClassificationFailure
}

func (e *TDXClassificationBatchError) Error() string {
	if e == nil || len(e.Failures) == 0 {
		return ""
	}
	first := e.Failures[0]
	return fmt.Sprintf("%d TDX classification families failed; first %s: %v", len(e.Failures), first.Family, first.Err)
}

func SyncTDXClassifications(ctx context.Context, db *sql.DB, source TDXClassificationSource) (TDXClassificationSummary, error) {
	return SyncTDXClassificationsWithOptions(ctx, db, source, TDXClassificationSyncOptions{})
}

func SyncTDXClassificationsWithOptions(ctx context.Context, db *sql.DB, source TDXClassificationSource, options TDXClassificationSyncOptions) (summary TDXClassificationSummary, retErr error) {
	if db == nil {
		return summary, fmt.Errorf("duckdb is nil")
	}
	if source == nil {
		return summary, fmt.Errorf("TDX source is nil")
	}
	now := time.Now()
	if options.Now != nil {
		now = options.Now()
	}
	if now.IsZero() {
		return summary, fmt.Errorf("classification observation time is zero")
	}
	local := now.In(chinaMarketZone)
	snapshotDate := time.Date(local.Year(), local.Month(), local.Day(), 0, 0, 0, 0, time.UTC)

	runID, err := duckstore.StartIngestRun(ctx, db, "tdx", tdxClassificationDataset, nil)
	if err != nil {
		return summary, fmt.Errorf("start TDX classification ingest run: %w", err)
	}
	summary.RunID = runID
	defer func() {
		finalizeTrackedRun(ctx, db, runID, classificationRunStatus(summary, retErr), &retErr)
	}()

	instruments, err := source.Instruments(ctx)
	if err != nil {
		return summary, fmt.Errorf("list TDX instruments: %w", err)
	}
	if _, err := duckstore.UpsertInstruments(ctx, db, instruments); err != nil {
		return summary, fmt.Errorf("refresh canonical instrument master: %w", err)
	}

	families := source.ClassificationFamilies()
	summary.Families = len(families)
	for i, family := range families {
		if err := ctx.Err(); err != nil {
			return summary, err
		}
		snapshot, err := source.ClassificationSnapshot(ctx, family)
		if err != nil {
			summary.Failures = append(summary.Failures, TDXClassificationFailure{Family: family, Err: err})
			reportClassificationProgress(options, summary, i+1, family)
			continue
		}
		if snapshot.Taxonomy.Source != "tdx" {
			err := fmt.Errorf("classification family %q returned source %q", family, snapshot.Taxonomy.Source)
			summary.Failures = append(summary.Failures, TDXClassificationFailure{Family: family, Err: err})
			reportClassificationProgress(options, summary, i+1, family)
			continue
		}
		result, err := duckstore.ApplyClassificationSnapshotForRun(ctx, db, runID, snapshotDate, now, snapshot)
		if err != nil {
			summary.Failures = append(summary.Failures, TDXClassificationFailure{Family: family, Err: err})
			reportClassificationProgress(options, summary, i+1, family)
			continue
		}
		summary.Synced++
		summary.Nodes += result.Nodes
		summary.Members += result.Members
		summary.Opened += result.Opened
		summary.Closed += result.Closed
		reportClassificationProgress(options, summary, i+1, family)
	}

	if len(summary.Failures) != 0 {
		return summary, &TDXClassificationBatchError{Failures: summary.Failures}
	}
	return summary, nil
}

func reportClassificationProgress(options TDXClassificationSyncOptions, summary TDXClassificationSummary, processed int, family string) {
	if options.OnProgress == nil {
		return
	}
	options.OnProgress(TDXClassificationProgress{
		RunID: summary.RunID,
		Processed: processed,
		Total: summary.Families,
		Synced: summary.Synced,
		Failed: len(summary.Failures),
		Family: family,
	})
}

func classificationRunStatus(summary TDXClassificationSummary, runErr error) string {
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
