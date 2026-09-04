package ingest

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"

	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

const canonicalFundamentalDataset = "fundamental_fact"

type FundamentalMaterializationSummary struct {
	RunID int64

	FilingResolutionAttempted int
	FilingResolutionRecovered int
	FilingResolutionPending   int

	LinkRecords   int
	Linked        int
	LinkPending   int
	LinkAmbiguous int
	LinksRemoved  int

	Candidates   int
	Materialized int
	Inserted     int
	Updated      int
	Removed      int
	Rejected     int
}

// MaterializeProviderFundamentals is a local-only derivation. It never performs
// network I/O: provider facts and authoritative filing evidence must already be
// present. Retained pending filings are first retried against the current temporal
// security master, then provider/filing links and canonical facts are reconciled.
func MaterializeProviderFundamentals(ctx context.Context, db *sql.DB, providerSource string) (summary FundamentalMaterializationSummary, retErr error) {
	if db == nil {
		return summary, errors.New("duckdb is nil")
	}
	providerSource = strings.TrimSpace(providerSource)
	if providerSource == "" {
		return summary, errors.New("provider source is required")
	}
	runID, err := duckstore.StartIngestRun(ctx, db, "alphalake", canonicalFundamentalDataset, nil)
	if err != nil {
		return summary, fmt.Errorf("start canonical fundamental run: %w", err)
	}
	summary.RunID = runID
	defer func() {
		finalizeTrackedRun(ctx, db, runID, fundamentalMaterializationRunStatus(summary, retErr), &retErr)
	}()

	filingResolution, err := duckstore.RefreshPendingFilingResolutions(ctx, db, runID, 500)
	if err != nil {
		return summary, fmt.Errorf("refresh pending filing resolutions: %w", err)
	}
	summary.FilingResolutionAttempted = filingResolution.Attempted
	summary.FilingResolutionRecovered = filingResolution.Resolved
	summary.FilingResolutionPending = filingResolution.StillPending

	links, err := duckstore.RefreshProviderFilingLinks(ctx, db, runID, providerSource)
	if err != nil {
		return summary, fmt.Errorf("refresh provider filing links: %w", err)
	}
	summary.LinkRecords = links.Records
	summary.Linked = links.Linked
	summary.LinkPending = links.Pending
	summary.LinkAmbiguous = links.Ambiguous
	summary.LinksRemoved = links.Removed

	facts, err := duckstore.MaterializeCanonicalFundamentals(ctx, db, runID, providerSource)
	if err != nil {
		return summary, fmt.Errorf("materialize canonical fundamentals: %w", err)
	}
	summary.Candidates = facts.Candidates
	summary.Materialized = facts.Materialized
	summary.Inserted = facts.Inserted
	summary.Updated = facts.Updated
	summary.Removed = facts.Removed
	summary.Rejected = facts.Rejected
	return summary, nil
}

func fundamentalMaterializationRunStatus(summary FundamentalMaterializationSummary, runErr error) string {
	if errors.Is(runErr, context.Canceled) || errors.Is(runErr, context.DeadlineExceeded) {
		return duckstore.IngestRunCanceled
	}
	if runErr != nil {
		if summary.FilingResolutionAttempted > 0 || summary.LinkRecords > 0 || summary.Candidates > 0 {
			return duckstore.IngestRunPartial
		}
		return duckstore.IngestRunFailed
	}
	if summary.FilingResolutionPending > 0 || summary.LinkPending > 0 || summary.LinkAmbiguous > 0 || summary.Rejected > 0 {
		return duckstore.IngestRunPartial
	}
	return duckstore.IngestRunCompleted
}
