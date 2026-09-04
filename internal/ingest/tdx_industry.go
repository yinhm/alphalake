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

const tdxIndustryDataset = "classification_industry"

type TDXIndustrySource interface {
	Instruments(context.Context) ([]domain.InstrumentObservation, error)
	IndustrySnapshots(context.Context) ([]domain.ClassificationSnapshot, error)
}

type TDXIndustrySummary struct {
	RunID      int64
	Taxonomies int
	Synced     int
	Nodes      int
	Members    int
	Opened     int
	Closed     int
	Failures   []TDXClassificationFailure
}

type TDXIndustryProgress struct {
	RunID     int64
	Processed int
	Total     int
	Synced    int
	Failed    int
	Taxonomy  string
}

type TDXIndustrySyncOptions struct {
	Now        func() time.Time
	OnProgress func(TDXIndustryProgress)
}

type TDXIndustryBatchError struct {
	Failures []TDXClassificationFailure
}

func (e *TDXIndustryBatchError) Error() string {
	if e == nil || len(e.Failures) == 0 {
		return ""
	}
	first := e.Failures[0]
	return fmt.Sprintf("%d TDX industry taxonomies failed; first %s: %v", len(e.Failures), first.Family, first.Err)
}

func SyncTDXIndustries(ctx context.Context, db *sql.DB, source TDXIndustrySource) (TDXIndustrySummary, error) {
	return SyncTDXIndustriesWithOptions(ctx, db, source, TDXIndustrySyncOptions{})
}

// SyncTDXIndustries downloads TDX industry assignments plus the shared industry
// dictionary once, then applies TDX and Shenwan taxonomies through the same
// temporal classification store used by block families.
func SyncTDXIndustriesWithOptions(ctx context.Context, db *sql.DB, source TDXIndustrySource, options TDXIndustrySyncOptions) (summary TDXIndustrySummary, retErr error) {
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
		return summary, fmt.Errorf("industry observation time is zero")
	}
	local := now.In(chinaMarketZone)
	snapshotDate := time.Date(local.Year(), local.Month(), local.Day(), 0, 0, 0, 0, time.UTC)

	runID, err := duckstore.StartIngestRun(ctx, db, "tdx", tdxIndustryDataset, nil)
	if err != nil {
		return summary, fmt.Errorf("start TDX industry ingest run: %w", err)
	}
	summary.RunID = runID
	defer func() {
		finalizeTrackedRun(ctx, db, runID, industryRunStatus(summary, retErr), &retErr)
	}()

	if _, _, err := refreshInstrumentMaster(ctx, db, source); err != nil {
		return summary, fmt.Errorf("refresh TDX instrument master: %w", err)
	}
	snapshots, err := source.IndustrySnapshots(ctx)
	if err != nil {
		return summary, fmt.Errorf("load TDX industry snapshots: %w", err)
	}
	if len(snapshots) == 0 {
		return summary, fmt.Errorf("TDX industry source returned no taxonomies")
	}
	summary.Taxonomies = len(snapshots)

	for i, snapshot := range snapshots {
		if err := ctx.Err(); err != nil {
			return summary, err
		}
		code := snapshot.Taxonomy.Code
		if snapshot.Taxonomy.Source != "tdx" {
			err := fmt.Errorf("industry taxonomy %q returned source %q", code, snapshot.Taxonomy.Source)
			summary.Failures = append(summary.Failures, TDXClassificationFailure{Family: code, Err: err})
			reportIndustryProgress(options, summary, i+1, code)
			continue
		}
		result, err := duckstore.ApplyClassificationSnapshotForRun(ctx, db, runID, snapshotDate, now, snapshot)
		if err != nil {
			summary.Failures = append(summary.Failures, TDXClassificationFailure{Family: code, Err: err})
			reportIndustryProgress(options, summary, i+1, code)
			continue
		}
		summary.Synced++
		summary.Nodes += result.Nodes
		summary.Members += result.Members
		summary.Opened += result.Opened
		summary.Closed += result.Closed
		reportIndustryProgress(options, summary, i+1, code)
	}

	if len(summary.Failures) != 0 {
		return summary, &TDXIndustryBatchError{Failures: summary.Failures}
	}
	return summary, nil
}

func reportIndustryProgress(options TDXIndustrySyncOptions, summary TDXIndustrySummary, processed int, taxonomy string) {
	if options.OnProgress == nil {
		return
	}
	options.OnProgress(TDXIndustryProgress{
		RunID: summary.RunID, Processed: processed, Total: summary.Taxonomies,
		Synced: summary.Synced, Failed: len(summary.Failures), Taxonomy: taxonomy,
	})
}

func industryRunStatus(summary TDXIndustrySummary, runErr error) string {
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
