package main

import (
	"context"
	"database/sql"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
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
	if helpRequested(args) {
		return true, errHelp
	}
	switch args[0] {
	case "sync-native-reference", "export-native-references", "upgrade-native-references":
		return true, runNativeReference(ctx, args[0], args[1:])
	case "financial-statements":
		return true, runFinancialStatements(ctx, args)
	case "tdx-financial-fields", "export-financial-source":
		return true, runSourceFinancial(ctx, args)
	case "sync-valuation-quotes":
		return true, runSyncQuoteWindow(ctx, args[1:])
	case "export-valuation-quote":
		return true, runValuationQuote(ctx, args[1:])
	case "export-industry-capital":
		return true, runIndustryCapitalExport(ctx, args[1:])
	case "export-wacc-references":
		return true, runWACCReferenceExport(ctx, args[1:])
	case "export-market-capital":
		return true, runMarketCapitalExport(ctx, args[1:])
	case "sync-share-classes", "sync-hkex-close", "sync-equity-proceeds":
		return true, runMarketSource(ctx, args[0], args[1:])
	case "sync-bse-code-transitions", "sync-company-industries", "sync-industry-capital", "sync-industry-beta", "sync-cny-yield", "sync-credit-spreads", "sync-hkd-cny":
		return true, runReferenceSync(ctx, args[0], args[1:])
	case "sync-country-risk":
		return true, runCountryRiskSync(ctx, args[1:])
	case "valuation-readiness":
		return true, runValuationReadiness(ctx, args[1:])
	case "export-valuation":
		return true, runValuationExport(ctx, args[1:])
	case "import-cninfo-document":
		return true, runDocumentImport(ctx, args[1:], true)
	case "import-reviewed-document":
		return true, runReviewedDocumentImport(ctx, args[1:])
	case "supplement-history":
		return true, runSupplementHistory(ctx, args[1:])
	case "import-supplements":
		return true, runSupplementImport(ctx, args[1:])
	case "filing-unresolved":
		return true, runFilingUnresolved(ctx, args[1:])
	case "upgrade-filing-coverage":
		return true, runCoverageUpgrade(ctx, args[1:])
	case "import-filing-coverage":
		return true, runCoverageImport(ctx, args[1:])
	case "export-prospectuses":
		return true, runExportProspectuses(ctx, args[1:])
	case "sync-filings":
		return true, runSyncFilings(ctx, args[1:])
	case "repair-filings":
		return true, runRepairFilings(ctx, args[1:])
	case "materialize-fundamentals":
		return true, runMaterializeFundamentals(ctx, args[1:])
	default:
		return false, nil
	}
}

func runFilingUnresolved(ctx context.Context, args []string) error {
	if len(args) == 0 || strings.TrimSpace(args[0]) == "" {
		return usageError("usage: alphalake filing-unresolved <db-path> [--limit N] [--offset N]")
	}
	limit, offset, err := parseResolutionPageArgs(args[1:])
	if err != nil {
		return parseError(err)
	}
	db, err := duckstore.OpenInitialized(ctx, args[0])
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
		return usageError("usage: alphalake sync-filings <db-path> [--all] [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--metadata-only] [--rescan] [--prospectus] [--code 600519]")
	}
	dbPath := strings.TrimSpace(args[0])
	if dbPath == "" {
		return usageError("database path is required")
	}
	fs := flag.NewFlagSet("sync-filings", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	all := fs.Bool("all", false, "backfill from 1990-01-01")
	startText := fs.String("start", "", "inclusive catalogue start date")
	endText := fs.String("end", "", "inclusive catalogue end date")
	codesFile := fs.String("codes-file", "", "one security code per line; serial batch on one database connection")
	prospectus := fs.Bool("prospectus", false, "collect IPO prospectuses for one security; period coverage requires separate review")
	metadataOnly := fs.Bool("metadata-only", false, "retain catalogue metadata without downloading filing documents")
	rescan := fs.Bool("rescan", false, "ignore completed old-window checkpoints")
	code := fs.String("code", "", "query one six-digit CNINFO security; checkpoint separately from whole-market windows")
	pageSize := fs.Int("page-size", 30, "CNINFO page size in [1,100]")
	windowDays := fs.Int("window-days", 90, "catalogue window size in [1,366]")
	if err := fs.Parse(args[1:]); err != nil {
		return parseError(err)
	}
	if len(fs.Args()) != 0 {
		return usageError("unexpected sync-filings arguments: %s", strings.Join(fs.Args(), " "))
	}
	if *all && strings.TrimSpace(*startText) != "" {
		return usageError("--all and --start are mutually exclusive")
	}
	var startDate time.Time
	var err error
	if *all {
		startDate = time.Date(1990, 1, 1, 0, 0, 0, 0, time.UTC)
	} else if strings.TrimSpace(*startText) != "" {
		startDate, err = parseCLIDate(*startText)
		if err != nil {
			return usageError("parse --start: %v", err)
		}
	}
	var endDate time.Time
	if strings.TrimSpace(*endText) != "" {
		endDate, err = parseCLIDate(*endText)
		if err != nil {
			return usageError("parse --end: %v", err)
		}
	}

	codes := []string{*code}
	if *codesFile != "" {
		if *code != "" {
			return usageError("--code and --codes-file are mutually exclusive")
		}
		raw, e := os.ReadFile(*codesFile)
		if e != nil {
			return e
		}
		codes = strings.Fields(string(raw))
		if len(codes) == 0 {
			return usageError("empty codes file")
		}
		for _, c := range codes {
			if len(c) != 6 || strings.Trim(c, "0123456789") != "" {
				return usageError("invalid security code %q", c)
			}
		}
	}
	if *prospectus && codes[0] == "" {
		return usageError("--prospectus requires --code or --codes-file")
	}
	db, err := duckstore.OpenInitialized(ctx, dbPath)
	if err != nil {
		return err
	}
	defer db.Close()
	source, err := cninfo.NewDefaultClient()
	if err != nil {
		return err
	}
	artifactRoot, err := duckstore.FinancialArchiveRoot(ctx, db)
	if err != nil {
		return err
	}
	lastPages := -1
	lastFailures := -1
	options := ingest.CNINFOFilingOptions{
		ProspectusOnly: *prospectus,
		Code:           *code,
		StartDate:      startDate,
		EndDate:        endDate,
		PageSize:       *pageSize,
		WindowDays:     *windowDays,
		MetadataOnly:   *metadataOnly,
		Rescan:         *rescan,
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
	var failures []error
	for _, securityCode := range codes {
		options.Code = securityCode
		lastPages, lastFailures = -1, -1
		summary, syncErr := ingest.SyncCNINFOFilingsWithOptions(ctx, db, source, artifactRoot, options)
		fmt.Printf("CNINFO filing sync: run=%d windows=%d skipped_windows=%d pages=%d filings=%d inserted=%d updated=%d resolved=%d pending=%d documents=%d reused_documents=%d issues=%d failures=%d metadata_only=%v raw=%s\n",
			summary.RunID, summary.Windows, summary.SkippedWindows, summary.Pages,
			summary.Filings, summary.Inserted, summary.Updated, summary.Resolved,
			summary.Pending, summary.Documents, summary.ReusedDocs, summary.Issues,
			len(summary.Failures), *metadataOnly, artifactRoot)
		for _, failure := range summary.Failures {
			fmt.Fprintf(os.Stderr, "CNINFO filing issue: run=%d code=%s window=%s page=%d filing=%s error=%q\n", summary.RunID, securityCode, failure.Window, failure.Page, failure.SourceFilingID, failure.Err)
		}
		if syncErr != nil {
			failures = append(failures, fmt.Errorf("%s: %w", securityCode, syncErr))
		}
	}
	return errors.Join(failures...)
}

func runRepairFilings(ctx context.Context, args []string) error {
	if len(args) < 1 {
		return usageError("usage: alphalake repair-filings <db-path> --period YYYY-MM-DD [--limit N]")
	}
	dbPath := strings.TrimSpace(args[0])
	if dbPath == "" {
		return usageError("database path is required")
	}
	fs := flag.NewFlagSet("repair-filings", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	periodText := fs.String("period", "", "quarter-end report period")
	limit := fs.Int("limit", 0, "maximum securities to attempt; zero attempts all pending codes")
	if err := fs.Parse(args[1:]); err != nil {
		return parseError(err)
	}
	if len(fs.Args()) != 0 || *limit < 0 {
		return usageError("invalid repair-filings arguments")
	}
	period, err := parseCLIDate(*periodText)
	if err != nil {
		return usageError("parse --period: %v", err)
	}
	local := time.Now().In(domain.ChinaDisclosureLocation)
	end := time.Date(local.Year(), local.Month(), local.Day(), 0, 0, 0, 0, time.UTC)
	if period.After(end) {
		return fmt.Errorf("report period is in the future")
	}
	db, err := duckstore.OpenInitialized(ctx, dbPath)
	if err != nil {
		return err
	}
	defer db.Close()
	queries, err := duckstore.PendingFilingRepairQueries(ctx, db, period)
	if err != nil {
		return err
	}
	total := len(queries)
	if *limit > 0 && len(queries) > *limit {
		queries = queries[:*limit]
	}
	client, err := cninfo.NewDefaultClient()
	if err != nil {
		return err
	}
	attempted, failed, err := repairFilingQueries(ctx, db, client, filepath.Dir(dbPath), queries, end)
	fmt.Printf("CNINFO filing repair: pending_codes=%d attempted=%d failed=%d unattempted=%d; rematerialize to verify remaining links\n", total, attempted, failed, total-attempted)
	if err != nil {
		return err
	}
	if failed > 0 {
		return fmt.Errorf("%d security filing repairs failed", failed)
	}
	return nil
}

func repairFilingQueries(ctx context.Context, db *sql.DB, source ingest.CNINFOFilingSource, root string, queries []duckstore.FilingRepairQuery, end time.Time) (attempted, failed int, err error) {
	for i, q := range queries {
		if err := ctx.Err(); err != nil {
			return attempted, failed, err
		}
		result, err := ingest.SyncCNINFOFilingsWithOptions(ctx, db, source, root, ingest.CNINFOFilingOptions{
			Code: q.Code, StartDate: q.StartDate, EndDate: end, WindowDays: 366, MetadataOnly: true, Rescan: true})
		attempted++
		if err != nil || result.Pending > 0 {
			failed++
		}
		fmt.Printf("CNINFO filing repair progress: security=%d/%d code=%s missing_periods=%d run=%d pages=%d inserted=%d updated=%d pending=%d failures=%d\n", i+1, len(queries), q.Code, q.MissingPeriods, result.RunID, result.Pages, result.Inserted, result.Updated, result.Pending, len(result.Failures))
		if err != nil {
			fmt.Fprintf(os.Stderr, "CNINFO filing repair issue: code=%s error=%q\n", q.Code, err)
		}
	}
	return attempted, failed, ctx.Err()
}

func runMaterializeFundamentals(ctx context.Context, args []string) error {
	if len(args) < 1 {
		return usageError("usage: alphalake materialize-fundamentals <db-path> [--field FN110]")
	}
	fs := flag.NewFlagSet("materialize-fundamentals", flag.ContinueOnError)
	field := fs.String("field", "", "only rebuild this field using existing filing links")
	if err := fs.Parse(args[1:]); err != nil {
		return parseError(err)
	}
	if fs.NArg() != 0 {
		return usageError("unexpected arguments")
	}
	var fields []string
	fs.Visit(func(f *flag.Flag) {
		if f.Name == "field" {
			fields = []string{*field}
		}
	})
	db, err := duckstore.OpenInitialized(ctx, args[0])
	if err != nil {
		return err
	}
	defer db.Close()
	summary, materializeErr := ingest.MaterializeProviderFundamentals(ctx, db, "tdx", fields...)
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

func parseFinancialLimit(args []string) (int, bool, string, error) {
	fs := flag.NewFlagSet("sync-financial", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	report := fs.String("report", "", "write structured source maintenance receipt to a new file")
	offline := fs.Bool("offline", false, "use local packages and identities without network")
	all := fs.Bool("all", false, "all past packages")
	latest := fs.Int("latest", 1, "newest N past packages")
	if err := fs.Parse(args); err != nil {
		return 0, false, "", parseError(err)
	}
	suppliedLatest := false
	fs.Visit(func(f *flag.Flag) {
		if f.Name == "latest" {
			suppliedLatest = true
		}
	})
	if fs.NArg() != 0 || *latest < 1 || (*all && suppliedLatest) {
		return 0, false, "", usageError("use either --all or --latest positive-N")
	}
	if *all {
		return 0, *offline, *report, nil
	}
	return *latest, *offline, *report, nil
}
