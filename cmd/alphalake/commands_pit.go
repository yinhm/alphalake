package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"path/filepath"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	"github.com/yinhm/alphalake/internal/ingest"
	"github.com/yinhm/alphalake/internal/source/cninfo"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

func runExtendedCommand(ctx context.Context, args []string) (bool, error) {
	if len(args) == 0 {
		return false, nil
	}
	switch args[0] {
	case "filing-unresolved":
		return true, runFilingUnresolved(ctx, args[1:])
	case "sync-filings":
		return true, runSyncFilings(ctx, args[1:])
	case "materialize-fundamentals":
		return true, runMaterializeFundamentals(ctx, args[1:])
	default:
		return false, nil
	}
}

func runFilingUnresolved(ctx context.Context, args []string) error {
	if len(args) == 0 || strings.TrimSpace(args[0]) == "" {
		return fmt.Errorf("usage: alphalake filing-unresolved <db-path> [--limit N] [--offset N]")
	}
	limit, offset, err := parseResolutionPageArgs(args[1:])
	if err != nil {
		return err
	}
	db, err := duckstore.OpenAndMigrate(ctx, args[0])
	if err != nil {
		return err
	}
	defer db.Close()
	rows, err := duckstore.ListFilingResolutionsPage(ctx, db, domain.FilingResolutionPending, limit, offset)
	if err != nil {
		return err
	}
	fmt.Printf("pending filing resolutions: %d (limit=%d offset=%d)\n", len(rows), limit, offset)
	for _, row := range rows {
		date := ""
		if row.AnnouncementDate != nil {
			date = row.AnnouncementDate.Format("2006-01-02")
		}
		fmt.Printf("  filing=%d source=%s source_id=%s code=%s exchange=%s date=%s precision=%s title=%q reason=%q\n",
			row.FilingID, row.Source, row.SourceFilingID, row.ProviderCode, row.ExchangeMIC,
			date, row.AnnouncementTimePrecision, row.Title, row.ResolutionReason)
	}
	return nil
}

func runSyncFilings(ctx context.Context, args []string) error {
	if len(args) < 1 {
		return fmt.Errorf("usage: alphalake sync-filings <db-path> [--all] [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--metadata-only] [--rescan]")
	}
	dbPath := strings.TrimSpace(args[0])
	if dbPath == "" {
		return fmt.Errorf("database path is required")
	}
	fs := flag.NewFlagSet("sync-filings", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	all := fs.Bool("all", false, "backfill from 1990-01-01")
	startText := fs.String("start", "", "inclusive catalogue start date")
	endText := fs.String("end", "", "inclusive catalogue end date")
	metadataOnly := fs.Bool("metadata-only", false, "retain catalogue metadata without downloading filing documents")
	rescan := fs.Bool("rescan", false, "ignore completed old-window checkpoints")
	pageSize := fs.Int("page-size", 50, "CNINFO page size in [1,100]")
	windowDays := fs.Int("window-days", 90, "catalogue window size in [1,366]")
	if err := fs.Parse(args[1:]); err != nil {
		return err
	}
	if len(fs.Args()) != 0 {
		return fmt.Errorf("unexpected sync-filings arguments: %s", strings.Join(fs.Args(), " "))
	}
	if *all && strings.TrimSpace(*startText) != "" {
		return fmt.Errorf("--all and --start are mutually exclusive")
	}
	var startDate time.Time
	var err error
	if *all {
		startDate = time.Date(1990, 1, 1, 0, 0, 0, 0, time.UTC)
	} else if strings.TrimSpace(*startText) != "" {
		startDate, err = parseCLIDate(*startText)
		if err != nil {
			return fmt.Errorf("parse --start: %w", err)
		}
	}
	var endDate time.Time
	if strings.TrimSpace(*endText) != "" {
		endDate, err = parseCLIDate(*endText)
		if err != nil {
			return fmt.Errorf("parse --end: %w", err)
		}
	}

	db, err := duckstore.OpenAndMigrate(ctx, dbPath)
	if err != nil {
		return err
	}
	defer db.Close()
	source, err := cninfo.NewDefaultClient()
	if err != nil {
		return err
	}
	artifactRoot := filepath.Join(filepath.Dir(dbPath), "raw")
	lastPages := -1
	lastFailures := -1
	options := ingest.CNINFOFilingOptions{
		StartDate:    startDate,
		EndDate:      endDate,
		PageSize:     *pageSize,
		WindowDays:   *windowDays,
		MetadataOnly: *metadataOnly,
		Rescan:       *rescan,
		OnProgress: func(progress ingest.CNINFOFilingProgress) {
			if progress.Pages != lastPages || progress.Failures != lastFailures {
				fmt.Printf("CNINFO filing progress: run=%d window=%s page=%d pages=%d filings=%d inserted=%d updated=%d resolved=%d pending=%d documents=%d reused=%d issues=%d failures=%d\n",
					progress.RunID, progress.Window, progress.Page, progress.Pages,
					progress.Filings, progress.Inserted, progress.Updated,
					progress.Resolved, progress.Pending, progress.Documents,
					progress.ReusedDocs, progress.Issues, progress.Failures)
			}
			lastPages = progress.Pages
			lastFailures = progress.Failures
		},
	}
	summary, syncErr := ingest.SyncCNINFOFilingsWithOptions(ctx, db, source, artifactRoot, options)
	fmt.Printf("CNINFO filing sync: run=%d windows=%d skipped_windows=%d pages=%d filings=%d inserted=%d updated=%d resolved=%d pending=%d documents=%d reused_documents=%d issues=%d failures=%d metadata_only=%v raw=%s\n",
		summary.RunID, summary.Windows, summary.SkippedWindows, summary.Pages,
		summary.Filings, summary.Inserted, summary.Updated, summary.Resolved,
		summary.Pending, summary.Documents, summary.ReusedDocs, summary.Issues,
		len(summary.Failures), *metadataOnly, artifactRoot)
	return syncErr
}

func runMaterializeFundamentals(ctx context.Context, args []string) error {
	if len(args) != 1 {
		return fmt.Errorf("usage: alphalake materialize-fundamentals <db-path>")
	}
	db, err := duckstore.OpenAndMigrate(ctx, args[0])
	if err != nil {
		return err
	}
	defer db.Close()
	summary, materializeErr := ingest.MaterializeProviderFundamentals(ctx, db, "tdx")
	fmt.Printf("fundamental materialization: run=%d filing_resolution_attempted=%d filing_resolution_recovered=%d filing_resolution_pending=%d link_records=%d linked=%d link_pending=%d link_ambiguous=%d links_removed=%d candidates=%d materialized=%d inserted=%d updated=%d removed=%d rejected=%d\n",
		summary.RunID,
		summary.FilingResolutionAttempted, summary.FilingResolutionRecovered, summary.FilingResolutionPending,
		summary.LinkRecords, summary.Linked, summary.LinkPending, summary.LinkAmbiguous, summary.LinksRemoved,
		summary.Candidates, summary.Materialized, summary.Inserted, summary.Updated, summary.Removed, summary.Rejected)
	return materializeErr
}

func parseCLIDate(value string) (time.Time, error) {
	parsed, err := time.Parse("2006-01-02", strings.TrimSpace(value))
	if err != nil {
		return time.Time{}, fmt.Errorf("expected YYYY-MM-DD: %w", err)
	}
	return parsed.UTC(), nil
}
