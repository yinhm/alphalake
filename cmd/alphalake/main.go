package main

import (
	"context"
	"fmt"
	"os"
	"os/signal"
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
	fmt.Fprintln(os.Stderr, "  status <db-path>")
}

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

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
		fmt.Printf("TDX daily sync: run=%d symbol=%s written=%d quarantined=%d\n",
			summary.RunID, os.Args[3], summary.Written, summary.Quarantined)
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
		fmt.Printf("TDX daily sync: run=%d instruments=%d attempted=%d synced=%d skipped=%d bars=%d quarantined=%d failures=%d\n",
			summary.RunID, summary.Instruments, summary.Attempted, summary.Synced, summary.Skipped,
			summary.Bars, summary.Quarantined, len(summary.Failures))
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
		fmt.Printf("TDX action sync: run=%d instruments=%d attempted=%d synced=%d skipped=%d actions=%d share_capital=%d failures=%d force=%v\n",
			summary.RunID, summary.Instruments, summary.Attempted, summary.Synced, summary.Skipped,
			summary.Actions, summary.ShareCapital, len(summary.Failures), force)
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
		fmt.Printf("TDX classification sync: run=%d families=%d synced=%d nodes=%d members=%d opened=%d closed=%d failures=%d\n",
			summary.RunID, summary.Families, summary.Synced, summary.Nodes, summary.Members,
			summary.Opened, summary.Closed, len(summary.Failures))
		if syncErr != nil {
			fatal(syncErr)
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

func fatal(err error) {
	fmt.Fprintln(os.Stderr, "error:", err)
	os.Exit(1)
}
