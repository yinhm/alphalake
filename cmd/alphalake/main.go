package main

import (
	"context"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/yinhm/alphalake/internal/ingest"
	tdxsource "github.com/yinhm/alphalake/internal/source/tdx"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

const version = "0.0.0-dev"

func usage() {
	fmt.Fprintln(os.Stderr, "usage: alphalake <command> [args]")
	fmt.Fprintln(os.Stderr, "commands:")
	fmt.Fprintln(os.Stderr, "  version")
	fmt.Fprintln(os.Stderr, "  schema")
	fmt.Fprintln(os.Stderr, "  init <db-path>")
	fmt.Fprintln(os.Stderr, "  sync-daily <db-path> <tdx-symbol>")
	fmt.Fprintln(os.Stderr, "  sync-daily-all <db-path>")
	fmt.Fprintln(os.Stderr, "  sync-actions <db-path> [--force]")
	fmt.Fprintln(os.Stderr, "  calc-adjustments <db-path>")
	fmt.Fprintln(os.Stderr, "  sync-classifications <db-path>")
	fmt.Fprintln(os.Stderr, "  sync-industries <db-path>")
	fmt.Fprintln(os.Stderr, "  sync-financial <db-path> [--all]")
	fmt.Fprintln(os.Stderr, "  sync-filings <db-path> [--all] [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--metadata-only] [--rescan]")
	fmt.Fprintln(os.Stderr, "  materialize-fundamentals <db-path>")
	fmt.Fprintln(os.Stderr, "  financial-unresolved <db-path> [--limit N] [--offset N]")
	fmt.Fprintln(os.Stderr, "  filing-unresolved <db-path> [--limit N] [--offset N]")
	fmt.Fprintln(os.Stderr, "  financial-ack <db-path> <artifact-id> <provider-code> <reason>")
	fmt.Fprintln(os.Stderr, "  financial-unack <db-path> <artifact-id> <provider-code>")
	fmt.Fprintln(os.Stderr, "  status <db-path>")
}

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	if handled, err := runExtendedCommand(ctx, os.Args[1:]); handled {
		if err != nil {
			fatal(err)
		}
		return
	}

	switch os.Args[1] {
	case "version":
		fmt.Println(version)

	case "schema":
		names, err := duckstore.Names()
		if err != nil {
			fatal(err)
		}
		for _, name := range names {
			fmt.Println(name)
		}

	case "init":
		if len(os.Args) != 3 {
			usage()
			os.Exit(2)
		}
		path := os.Args[2]
		db, err := duckstore.OpenAndMigrate(ctx, path)
		if err != nil {
			fatal(err)
		}
		if err := db.Close(); err != nil {
			fatal(err)
		}
		fmt.Printf("initialized DuckDB: %s\n", path)

	case "sync-daily":
		if len(os.Args) != 4 {
			usage()
			os.Exit(2)
		}
		db, err := duckstore.OpenAndMigrate(ctx, os.Args[2])
		if err != nil {
			fatal(err)
		}
		defer db.Close()

		source, err := tdxsource.DialDefault()
		if err != nil {
			fatal(err)
		}
		defer source.Close()

		summary, err := ingest.SyncTDXDailyWithSummary(ctx, db, source, os.Args[3])
		fmt.Printf("TDX daily sync: run=%d symbol=%s written=%d quarantined=%d master_failures=%d\n",
			summary.RunID, os.Args[3], summary.Written, summary.Quarantined, len(summary.MasterFailures))
		if err != nil {
			fatal(err)
		}

	case "sync-daily-all":
		if len(os.Args) != 3 {
			usage()
			os.Exit(2)
		}
		db, err := duckstore.OpenAndMigrate(ctx, os.Args[2])
		if err != nil {
			fatal(err)
		}
		defer db.Close()

		source, err := tdxsource.DialDefault()
		if err != nil {
			fatal(err)
		}
		defer source.Close()

		lastFailures := 0
		lastQuarantined := 0
		options := ingest.TDXDailySyncOptions{OnProgress: func(p ingest.TDXDailyProgress) {
			if p.Processed%100 == 0 || p.Processed == p.Total || p.Failed > lastFailures || p.Quarantined > lastQuarantined {
				fmt.Printf("TDX daily progress: run=%d %d/%d synced=%d failed=%d quarantined=%d current=%s\n",
					p.RunID, p.Processed, p.Total, p.Synced, p.Failed, p.Quarantined, p.Symbol)
			}
			lastFailures = p.Failed
			lastQuarantined = p.Quarantined
		}}
		summary, syncErr := ingest.SyncAllTDXDailyWithOptions(ctx, db, source, options)
		fmt.Printf("TDX daily sync: run=%d instruments=%d attempted=%d synced=%d skipped=%d bars=%d quarantined=%d failures=%d master_failures=%d\n",
			summary.RunID, summary.Instruments, summary.Attempted, summary.Synced, summary.Skipped,
			summary.Bars, summary.Quarantined, len(summary.Failures), len(summary.MasterFailures))
		if syncErr != nil {
			fatal(syncErr)
		}

	case "sync-actions":
		if len(os.Args) != 3 && len(os.Args) != 4 {
			usage()
			os.Exit(2)
		}
		force := false
		if len(os.Args) == 4 {
			if os.Args[3] != "--force" {
				usage()
				os.Exit(2)
			}
			force = true
		}
		db, err := duckstore.OpenAndMigrate(ctx, os.Args[2])
		if err != nil {
			fatal(err)
		}
		defer db.Close()

		source, err := tdxsource.DialDefault()
		if err != nil {
			fatal(err)
		}
		defer source.Close()

		lastFailures := 0
		options := ingest.TDXCorporateActionSyncOptions{
			ForceReplace: force,
			OnProgress: func(p ingest.TDXCorporateActionProgress) {
				if p.Processed%100 == 0 || p.Processed == p.Total || p.Failed > lastFailures {
					fmt.Printf("TDX action progress: run=%d %d/%d synced=%d failed=%d current=%s\n",
						p.RunID, p.Processed, p.Total, p.Synced, p.Failed, p.Symbol)
				}
				lastFailures = p.Failed
			},
		}
		summary, syncErr := ingest.SyncTDXCorporateActionsWithOptions(ctx, db, source, options)
		fmt.Printf("TDX action sync: run=%d instruments=%d attempted=%d synced=%d skipped=%d actions=%d share_capital=%d failures=%d master_failures=%d force=%v\n",
			summary.RunID, summary.Instruments, summary.Attempted, summary.Synced, summary.Skipped,
			summary.Actions, summary.ShareCapital, len(summary.Failures), len(summary.MasterFailures), force)
		if syncErr != nil {
			fatal(syncErr)
		}

	case "calc-adjustments":
		if len(os.Args) != 3 {
			usage()
			os.Exit(2)
		}
		db, err := duckstore.OpenAndMigrate(ctx, os.Args[2])
		if err != nil {
			fatal(err)
		}
		defer db.Close()

		lastFailures := 0
		options := ingest.AdjustmentOptions{OnProgress: func(p ingest.AdjustmentProgress) {
			if p.Processed%100 == 0 || p.Processed == p.Total || p.Failed > lastFailures {
				fmt.Printf("adjustment progress: run=%d %d/%d calculated=%d failed=%d current=%s\n",
					p.RunID, p.Processed, p.Total, p.Calculated, p.Failed, p.Symbol)
			}
			lastFailures = p.Failed
		}}
		summary, calcErr := ingest.CalculateTDXAdjustmentsWithOptions(ctx, db, options)
		fmt.Printf("adjustment calculation: run=%d instruments=%d attempted=%d calculated=%d skipped=%d segments=%d failures=%d\n",
			summary.RunID, summary.Instruments, summary.Attempted, summary.Calculated, summary.Skipped,
			summary.Segments, len(summary.Failures))
		if calcErr != nil {
			fatal(calcErr)
		}

	case "sync-classifications":
		if len(os.Args) != 3 {
			usage()
			os.Exit(2)
		}
		db, err := duckstore.OpenAndMigrate(ctx, os.Args[2])
		if err != nil {
			fatal(err)
		}
		defer db.Close()

		source, err := tdxsource.DialDefault()
		if err != nil {
			fatal(err)
		}
		defer source.Close()

		options := ingest.TDXClassificationSyncOptions{OnProgress: func(p ingest.TDXClassificationProgress) {
			fmt.Printf("TDX classification progress: run=%d %d/%d synced=%d failed=%d family=%s\n",
				p.RunID, p.Processed, p.Total, p.Synced, p.Failed, p.Family)
		}}
		summary, syncErr := ingest.SyncTDXClassificationsWithOptions(ctx, db, source, options)
		fmt.Printf("TDX classification sync: run=%d families=%d synced=%d nodes=%d members=%d opened=%d closed=%d failures=%d master_failures=%d\n",
			summary.RunID, summary.Families, summary.Synced, summary.Nodes, summary.Members,
			summary.Opened, summary.Closed, len(summary.Failures), len(summary.MasterFailures))
		if syncErr != nil {
			fatal(syncErr)
		}

	case "sync-industries":
		if len(os.Args) != 3 {
			usage()
			os.Exit(2)
		}
		db, err := duckstore.OpenAndMigrate(ctx, os.Args[2])
		if err != nil {
			fatal(err)
		}
		defer db.Close()

		source, err := tdxsource.DialDefault()
		if err != nil {
			fatal(err)
		}
		defer source.Close()

		options := ingest.TDXIndustrySyncOptions{OnProgress: func(p ingest.TDXIndustryProgress) {
			fmt.Printf("TDX industry progress: run=%d %d/%d synced=%d failed=%d taxonomy=%s\n",
				p.RunID, p.Processed, p.Total, p.Synced, p.Failed, p.Taxonomy)
		}}
		summary, syncErr := ingest.SyncTDXIndustriesWithOptions(ctx, db, source, options)
		fmt.Printf("TDX industry sync: run=%d taxonomies=%d synced=%d nodes=%d members=%d opened=%d closed=%d failures=%d master_failures=%d\n",
			summary.RunID, summary.Taxonomies, summary.Synced, summary.Nodes, summary.Members,
			summary.Opened, summary.Closed, len(summary.Failures), len(summary.MasterFailures))
		if syncErr != nil {
			fatal(syncErr)
		}

	case "sync-financial":
		if len(os.Args) != 3 && len(os.Args) != 4 {
			usage()
			os.Exit(2)
		}
		all := false
		if len(os.Args) == 4 {
			if os.Args[3] != "--all" {
				usage()
				os.Exit(2)
			}
			all = true
		}
		dbPath := os.Args[2]
		db, err := duckstore.OpenAndMigrate(ctx, dbPath)
		if err != nil {
			fatal(err)
		}
		defer db.Close()

		source, err := tdxsource.DialDefault()
		if err != nil {
			fatal(err)
		}
		defer source.Close()

		artifactRoot := filepath.Join(filepath.Dir(dbPath), "raw")
		maxPackages := 1
		if all {
			maxPackages = 0
		}
		lastFailures := 0
		lastUnresolved := 0
		options := ingest.TDXProfessionalFinancialOptions{
			MaxPackages: maxPackages,
			OnProgress: func(p ingest.TDXProfessionalFinancialProgress) {
				if p.Processed == p.Total || p.Failures > lastFailures || p.Unresolved > lastUnresolved {
					fmt.Printf("TDX financial progress: run=%d %d/%d packages=%d skipped=%d facts_attempted=%d facts_inserted=%d facts_reassigned=%d facts_removed=%d unresolved=%d acknowledged=%d failed=%d current=%s\n",
						p.RunID, p.Processed, p.Total, p.Packages, p.Skipped, p.FactsAttempted, p.FactsInserted,
						p.FactsReassigned, p.FactsRemoved, p.Unresolved, p.Acknowledged, p.Failures, p.Package)
				}
				lastFailures = p.Failures
				lastUnresolved = p.Unresolved
			},
		}
		summary, syncErr := ingest.SyncTDXProfessionalFinancialWithOptions(ctx, db, source, artifactRoot, options)
		fmt.Printf("TDX financial sync: run=%d listed=%d selected=%d packages=%d skipped=%d facts_attempted=%d facts_inserted=%d facts_reassigned=%d facts_removed=%d unresolved=%d acknowledged=%d failures=%d master_failures=%d all=%v raw=%s\n",
			summary.RunID, summary.Listed, summary.Selected, summary.Packages, summary.Skipped,
			summary.FactsAttempted, summary.FactsInserted, summary.FactsReassigned, summary.FactsRemoved,
			summary.Unresolved, summary.Acknowledged, len(summary.Failures), len(summary.MasterFailures), all, artifactRoot)
		if syncErr != nil {
			fatal(syncErr)
		}

	case "financial-unresolved":
		if len(os.Args) < 3 {
			usage()
			os.Exit(2)
		}
		limit, offset, err := parseResolutionPageArgs(os.Args[3:])
		if err != nil {
			fatal(err)
		}
		db, err := duckstore.OpenAndMigrate(ctx, os.Args[2])
		if err != nil {
			fatal(err)
		}
		defer db.Close()
		rows, err := duckstore.ListProviderFinancialResolutionsPage(ctx, db, duckstore.ProviderResolutionPending, limit, offset)
		if err != nil {
			fatal(err)
		}
		if len(rows) == 0 {
			fmt.Printf("pending financial resolutions: none (limit=%d offset=%d)\n", limit, offset)
			break
		}
		fmt.Printf("pending financial resolutions: %d (limit=%d offset=%d)\n", len(rows), limit, offset)
		for _, row := range rows {
			fmt.Printf("  artifact=%d file=%s period=%s code=%s marker=%d reason=%s\n",
				row.ArtifactID, row.SourceFile, row.ReportPeriod.Format("2006-01-02"), row.ProviderCode, row.MarketMarker, row.Reason)
		}

	case "financial-ack":
		if len(os.Args) < 6 {
			usage()
			os.Exit(2)
		}
		artifactID, err := strconv.ParseInt(os.Args[3], 10, 64)
		if err != nil || artifactID <= 0 {
			fatal(fmt.Errorf("invalid artifact ID %q", os.Args[3]))
		}
		reason := strings.TrimSpace(strings.Join(os.Args[5:], " "))
		if reason == "" {
			fatal(fmt.Errorf("acknowledgement reason is required"))
		}
		db, err := duckstore.OpenAndMigrate(ctx, os.Args[2])
		if err != nil {
			fatal(err)
		}
		defer db.Close()
		changed, err := duckstore.AcknowledgeProviderFinancialResolution(ctx, db, artifactID, os.Args[4], reason)
		if err != nil {
			fatal(err)
		}
		if changed {
			fmt.Printf("acknowledged financial resolution: artifact=%d code=%s\n", artifactID, os.Args[4])
			fmt.Println("rerun sync-financial so the package can be checkpointed if no pending records remain")
		} else {
			fmt.Printf("financial resolution already acknowledged: artifact=%d code=%s\n", artifactID, os.Args[4])
		}

	case "financial-unack":
		if len(os.Args) != 5 {
			usage()
			os.Exit(2)
		}
		artifactID, err := strconv.ParseInt(os.Args[3], 10, 64)
		if err != nil || artifactID <= 0 {
			fatal(fmt.Errorf("invalid artifact ID %q", os.Args[3]))
		}
		db, err := duckstore.OpenAndMigrate(ctx, os.Args[2])
		if err != nil {
			fatal(err)
		}
		defer db.Close()
		changed, err := duckstore.UnacknowledgeProviderFinancialResolution(ctx, db, artifactID, os.Args[4])
		if err != nil {
			fatal(err)
		}
		if changed {
			fmt.Printf("unacknowledged financial resolution: artifact=%d code=%s\n", artifactID, os.Args[4])
			fmt.Println("package checkpoint invalidated; rerun sync-financial to re-evaluate the record")
		} else {
			fmt.Printf("financial resolution already pending: artifact=%d code=%s\n", artifactID, os.Args[4])
		}

	case "status":
		if len(os.Args) != 3 {
			usage()
			os.Exit(2)
		}
		if _, err := os.Stat(os.Args[2]); err != nil {
			fatal(fmt.Errorf("stat database %q: %w", os.Args[2], err))
		}
		db, err := duckstore.Open(ctx, os.Args[2])
		if err != nil {
			fatal(err)
		}
		defer db.Close()
		status, err := duckstore.ReadOperationalStatus(ctx, db, 10)
		if err != nil {
			fatal(err)
		}
		fmt.Printf("AlphaLake %s\n", version)
		fmt.Printf("database: %s\n", os.Args[2])
		fmt.Printf("schema: %d/%d", status.SchemaVersion, status.LatestSchemaVersion)
		if pending := status.LatestSchemaVersion - status.SchemaVersion; pending > 0 {
			fmt.Printf(" (%d pending)", pending)
		}
		fmt.Println()
		fmt.Printf("validation failures: %d\n", status.ValidationFailures)
		fmt.Printf("checkpoints: %d\n", status.Checkpoints)
		if len(status.RecentRuns) == 0 {
			fmt.Println("recent runs: none")
			break
		}
		fmt.Println("recent runs:")
		for _, run := range status.RecentRuns {
			finished := "-"
			if run.FinishedAt != nil {
				finished = run.FinishedAt.Format(time.RFC3339)
			}
			fmt.Printf("  %d %s/%s status=%s started=%s finished=%s\n",
				run.RunID, run.Source, run.Dataset, run.Status,
				run.StartedAt.Format(time.RFC3339), finished)
		}

	default:
		usage()
		os.Exit(2)
	}
}

func parseResolutionPageArgs(args []string) (int, int, error) {
	limit, offset := 100, 0
	if len(args)%2 != 0 {
		return 0, 0, fmt.Errorf("resolution options must be --limit N and/or --offset N")
	}
	for i := 0; i < len(args); i += 2 {
		value, err := strconv.Atoi(args[i+1])
		if err != nil {
			return 0, 0, fmt.Errorf("invalid %s value %q", args[i], args[i+1])
		}
		switch args[i] {
		case "--limit":
			if value <= 0 {
				return 0, 0, fmt.Errorf("--limit must be positive")
			}
			limit = value
		case "--offset":
			if value < 0 {
				return 0, 0, fmt.Errorf("--offset must be non-negative")
			}
			offset = value
		default:
			return 0, 0, fmt.Errorf("unsupported resolution option %q", args[i])
		}
	}
	return limit, offset, nil
}

func fatal(err error) {
	fmt.Fprintln(os.Stderr, "error:", err)
	os.Exit(1)
}
