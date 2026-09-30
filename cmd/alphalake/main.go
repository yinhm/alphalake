package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	"github.com/yinhm/alphalake/internal/ingest"
	tdxsource "github.com/yinhm/alphalake/internal/source/tdx"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

const version = "0.0.0-dev"

// 帮助请求以退出码 0 打印 usage；参数解析/用法错误以退出码 2 打印 usage。
// 两者都必须在打开数据库或触网之前被拒绝。
var (
	errHelp  = errors.New("alphalake: help requested")
	errUsage = errors.New("alphalake: invalid arguments")
)

func isHelpFlag(arg string) bool {
	switch arg {
	case "-h", "--help", "help":
		return true
	}
	return false
}

// 每个命令的第一个位置参数都是 db-path，帮助标志落在命令名或该位置必是误用
// （alphalake --help / alphalake sync-filings --help）。
func helpRequested(args []string) bool {
	if len(args) == 0 {
		return false
	}
	return isHelpFlag(args[0]) || (len(args) > 1 && isHelpFlag(args[1]))
}

// parseError 归类命令行解析失败：flag 包对未定义 -h/--help 返回 ErrHelp，
// 其余（非法标志、意外参数、畸形标志值）为用法错误。
func parseError(err error) error {
	if err == nil {
		return nil
	}
	if errors.Is(err, flag.ErrHelp) {
		return errHelp
	}
	return fmt.Errorf("%w: %v", errUsage, err)
}

func usageError(format string, args ...any) error {
	return fmt.Errorf("%w: %s", errUsage, fmt.Sprintf(format, args...))
}

func usage() {
	fmt.Fprintln(os.Stderr, "usage: alphalake <command> [args] [--include-bse] (default: Shanghai/Shenzhen)")
	fmt.Fprintln(os.Stderr, "commands:")
	fmt.Fprintln(os.Stderr, "  financial-statements <db-path> <code> --period YYYY-MM-DD --as-of RFC3339 [--include-evidence]")
	fmt.Fprintln(os.Stderr, "  tdx-financial-fields (source dictionary, not valuation eligibility)")
	fmt.Fprintln(os.Stderr, "  export-financial-source <db-path> <six-digit-code> --period YYYY-MM-DD")
	fmt.Fprintln(os.Stderr, "  export-valuation-quote <db-path> <tdx-symbol> --date YYYY-MM-DD --as-of RFC3339")
	fmt.Fprintln(os.Stderr, "  export-wacc-references <db-path> --as-of RFC3339 [--latest | --country-release N --beta-release N --yield-release N [--credit-release N]] [--recorded-cutoff RFC3339]")
	fmt.Fprintln(os.Stderr, "  sync-industry-beta <db-path> [--offline] [--python executable] [--parser path]")
	fmt.Fprintln(os.Stderr, "  sync-credit-spreads <db-path> [--python python3] [--parser path] [--offline]")
	fmt.Fprintln(os.Stderr, "  export-market-capital <db-path> <code> --date YYYY-MM-DD --as-of RFC3339 --share-release N [--hk-release N --fx-release N]")
	fmt.Fprintln(os.Stderr, "  sync-equity-proceeds <db-path> <ipo|greenshoe> [--python path] [--offline]\n  sync-share-classes <db-path> <300866|600519> [--python path] [--offline]")
	fmt.Fprintln(os.Stderr, "  sync-hkex-close <db-path> <YYYY-MM-DD> [--python path] [--offline]")
	fmt.Fprintln(os.Stderr, "  sync-hkd-cny <db-path> [--offline] [--python executable] [--parser path]")
	fmt.Fprintln(os.Stderr, "  sync-cny-yield <db-path> [--offline] [--python executable] [--parser path]")
	fmt.Fprintln(os.Stderr, "  export-industry-capital <db-path> --as-of RFC3339 [--release N] [--recorded-cutoff RFC3339]")
	fmt.Fprintln(os.Stderr, "  sync-bse-code-transitions <db-path> [--offline] [--python executable] [--parser path]")
	fmt.Fprintln(os.Stderr, "  sync-company-industries <db-path> [--offline] [--python executable] [--parser path]")
	fmt.Fprintln(os.Stderr, "  sync-industry-capital <db-path> [--offline] [--python executable] [--parser path]")
	fmt.Fprintln(os.Stderr, "  upgrade-native-references <schema54-db> (explicit one-time schema55 upgrade)")
	fmt.Fprintln(os.Stderr, "  extend-capital-history <source-db> <new-candidate-db> (offline copy and historical capital rebuild)")
	fmt.Fprintln(os.Stderr, "  sync-native-reference <db-path> --file workbook-stem [--local file | --offline] [--python executable] [--parser path]")
	fmt.Fprintln(os.Stderr, "  export-native-references <db-path> --as-of RFC3339")
	fmt.Fprintln(os.Stderr, "  sync-country-risk <db-path> [--local file | --offline] [--python executable] [--parser path]")
	fmt.Fprintln(os.Stderr, "  version")
	fmt.Fprintln(os.Stderr, "  schema")
	fmt.Fprintln(os.Stderr, "  export-financial-snapshot <db-path> --output <new-directory> --fields <standard-names> --from YYYY-MM-DD --period YYYY-MM-DD --as-of RFC3339 [--codes 300866,600519]")
	fmt.Fprintln(os.Stderr, "  init <db-path>")
	fmt.Fprintln(os.Stderr, "  sync-daily <db-path> <tdx-symbol>")
	fmt.Fprintln(os.Stderr, "  sync-valuation-quotes <db-path> --symbols sh600004,... --period YYYY-MM-DD")
	fmt.Fprintln(os.Stderr, "  sync-instruments <db-path>")
	fmt.Fprintln(os.Stderr, "  sync-daily-all <db-path> [--symbols sh600519,sz002032,...]")
	fmt.Fprintln(os.Stderr, "  sync-actions <db-path> [--force]")
	fmt.Fprintln(os.Stderr, "  calc-adjustments <db-path>")
	fmt.Fprintln(os.Stderr, "  sync-classifications <db-path>")
	fmt.Fprintln(os.Stderr, "  sync-industries <db-path>")
	fmt.Fprintln(os.Stderr, "  sync-financial <db-path> [--all | --latest N] [--offline] [--report path]")
	fmt.Fprintln(os.Stderr, "  upgrade-financial-availability <schema55-db> (explicit one-time schema56 upgrade)")
	fmt.Fprintln(os.Stderr, "  upgrade-filing-coverage <schema52-db> (explicit one-time schema53 upgrade)")
	fmt.Fprintln(os.Stderr, "  export-prospectuses <db-path>")
	fmt.Fprintln(os.Stderr, "  import-filing-coverage <db-path> <reviews.json>")
	fmt.Fprintln(os.Stderr, "  sync-filings <db-path> [--all] [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--metadata-only] [--rescan] [--prospectus] [--code 600519 | --codes-file path]")
	fmt.Fprintln(os.Stderr, "  repair-filings <db-path> --period YYYY-MM-DD [--limit N]")
	fmt.Fprintln(os.Stderr, "  materialize-fundamentals <db-path> [--field FN110]")
	fmt.Fprintln(os.Stderr, "  financial-unresolved <db-path> [--limit N] [--offset N]")
	fmt.Fprintln(os.Stderr, "  filing-unresolved <db-path> [--limit N] [--offset N]")
	fmt.Fprintln(os.Stderr, "  financial-ack <db-path> <artifact-id> <provider-code> <reason>")
	fmt.Fprintln(os.Stderr, "  financial-unack <db-path> <artifact-id> <provider-code>")
	fmt.Fprintln(os.Stderr, "  status <db-path>")
	fmt.Fprintln(os.Stderr, "  import-cninfo-document <db-path> <artifact-root> <receipt-json> <pdf-file>")
	fmt.Fprintln(os.Stderr, "  import-reviewed-document <db-path> <artifact-root> <review-json> <pdf-file>")
	fmt.Fprintln(os.Stderr, "  supplement-history <db-path> <six-digit-code>")
	fmt.Fprintln(os.Stderr, "  import-supplements <db-path> <reviewed-json-file>")
	fmt.Fprintln(os.Stderr, "  valuation-readiness <db-path> --period YYYY-MM-DD --as-of RFC3339")
	fmt.Fprintln(os.Stderr, "  export-valuation <db-path> <six-digit-code> --period YYYY-MM-DD --as-of RFC3339")
}

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	// Explicit opt-in applies to the entire command, including nested syncs.
	args := os.Args[:1]
	for _, arg := range os.Args[1:] {
		if arg == "--include-bse" {
			ctx = domain.WithBSE(ctx)
		} else {
			args = append(args, arg)
		}
	}
	os.Args = args
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	if helpRequested(os.Args[1:]) {
		usage()
		os.Exit(0)
	}

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
		fmt.Printf("schema %d (current baseline)\n", duckstore.SchemaVersion)

	case "export-financial-snapshot":
		if err := runFinancialSnapshotExport(ctx, os.Args[2:]); err != nil {
			fatal(err)
		}

	case "init":
		if len(os.Args) != 3 {
			usage()
			os.Exit(2)
		}
		path := os.Args[2]
		db, err := duckstore.OpenInitialized(ctx, path)
		if err != nil {
			fatal(err)
		}
		if err := db.Close(); err != nil {
			fatal(err)
		}
		fmt.Printf("initialized DuckDB: %s\n", path)

	case "sync-instruments":
		if len(os.Args) != 3 {
			usage()
			os.Exit(2)
		}
		db, err := duckstore.OpenInitialized(ctx, os.Args[2])
		if err != nil {
			fatal(err)
		}
		defer db.Close()
		source, err := tdxsource.DialDefault()
		if err != nil {
			fatal(err)
		}
		defer source.Close()
		result, err := ingest.SyncTDXInstrumentMaster(ctx, db, source)
		fmt.Printf("instrument master: instruments=%d partition_failures=%d\n", len(result.Observations), len(result.Failures))
		if err != nil {
			fatal(err)
		}

	case "sync-daily":
		if len(os.Args) != 4 {
			usage()
			os.Exit(2)
		}
		db, err := duckstore.OpenInitialized(ctx, os.Args[2])
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
		if len(os.Args) != 3 && (len(os.Args) != 5 || os.Args[3] != "--symbols") {
			usage()
			os.Exit(2)
		}
		var symbols []string
		if len(os.Args) == 5 {
			symbols = strings.Split(os.Args[4], ",")
			for _, symbol := range symbols {
				key, err := tdxsource.NormalizeSymbol(symbol)
				if err != nil || key.ProviderSymbol != symbol {
					fatal(fmt.Errorf("invalid TDX symbol %q", symbol))
				}
			}
		}
		db, err := duckstore.OpenInitialized(ctx, os.Args[2])
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
		options := ingest.TDXDailySyncOptions{Symbols: symbols, OnProgress: func(p ingest.TDXDailyProgress) {
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
		db, err := duckstore.OpenInitialized(ctx, os.Args[2])
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
		db, err := duckstore.OpenInitialized(ctx, os.Args[2])
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
		db, err := duckstore.OpenInitialized(ctx, os.Args[2])
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
		for _, failure := range summary.Failures {
			fmt.Fprintf(os.Stderr, "TDX classification issue: run=%d taxonomy=%s error=%q\n", summary.RunID, failure.Family, failure.Err)
		}
		if syncErr != nil {
			fatal(syncErr)
		}

	case "sync-industries":
		if len(os.Args) != 3 {
			usage()
			os.Exit(2)
		}
		db, err := duckstore.OpenInitialized(ctx, os.Args[2])
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
		for _, failure := range summary.Failures {
			fmt.Fprintf(os.Stderr, "TDX industry issue: run=%d taxonomy=%s error=%q\n", summary.RunID, failure.Family, failure.Err)
		}
		if syncErr != nil {
			fatal(syncErr)
		}

	case "sync-financial":
		if len(os.Args) < 3 {
			usage()
			os.Exit(2)
		}
		maxPackages, offline, reportPath, err := parseFinancialLimit(os.Args[3:])
		if err != nil {
			fatal(err)
		}
		if reportPath != "" {
			if _, err := os.Lstat(reportPath); !errors.Is(err, os.ErrNotExist) {
				fatal(fmt.Errorf("source report must be a new file: %s", reportPath))
			}
		}
		all := maxPackages == 0
		dbPath := os.Args[2]
		db, err := duckstore.OpenInitialized(ctx, dbPath)
		if err != nil {
			fatal(err)
		}
		defer db.Close()

		source := new(tdxsource.Client)
		if !offline {
			source, err = tdxsource.DialDefault()
			if err != nil {
				fatal(err)
			}
			defer source.Close()
		}

		artifactRoot := filepath.Dir(dbPath)
		lastFailures := 0
		lastUnresolved := 0
		options := ingest.TDXProfessionalFinancialOptions{
			MaxPackages: maxPackages,
			Offline:     offline,
			OnProgress: func(p ingest.TDXProfessionalFinancialProgress) {
				if p.Error != "" {
					fmt.Fprintf(os.Stderr, "TDX financial package failed: run=%d package=%s error=%q\n", p.RunID, p.Package, p.Error)
				}
				if p.Processed == p.Total || p.Failures > lastFailures || p.Unresolved > lastUnresolved {
					fmt.Printf("TDX financial progress: run=%d %d/%d packages=%d skipped=%d records_attempted=%d records_inserted=%d records_reassigned=%d records_unresolved=%d unresolved=%d acknowledged=%d failed=%d current=%s\n",
						p.RunID, p.Processed, p.Total, p.Packages, p.Skipped, p.RecordsAttempted, p.RecordsInserted,
						p.RecordsReassigned, p.RecordsUnresolved, p.Unresolved, p.Acknowledged, p.Failures, p.Package)
				}
				lastFailures = p.Failures
				lastUnresolved = p.Unresolved
			},
		}
		summary, syncErr := ingest.SyncTDXProfessionalFinancialWithOptions(ctx, db, source, artifactRoot, options)
		if reportPath != "" {
			if err := writeFinancialSyncReport(ctx, db, reportPath, summary, syncErr, offline); err != nil {
				fatal(err)
			}
		}
		fmt.Printf("TDX financial sync: run=%d listed=%d selected=%d packages=%d skipped=%d records_attempted=%d records_inserted=%d records_reassigned=%d records_unresolved=%d unresolved=%d acknowledged=%d failures=%d master_failures=%d cache_fallbacks=%d all=%v root=%s\n",
			summary.RunID, summary.Listed, summary.Selected, summary.Packages, summary.Skipped,
			summary.RecordsAttempted, summary.RecordsInserted, summary.RecordsReassigned, summary.RecordsUnresolved,
			summary.Unresolved, summary.Acknowledged, len(summary.Failures), len(summary.MasterFailures), summary.CacheFallbacks, all, artifactRoot)
		if syncErr != nil {
			fatal(syncErr)
		}

		if summary.Unresolved > 0 || len(summary.MasterFailures) > 0 {
			fatal(fmt.Errorf("financial sync is partial: unresolved=%d master_failures=%d; inspect financial-unresolved/status", summary.Unresolved, len(summary.MasterFailures)))
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
		db, err := duckstore.OpenInitialized(ctx, os.Args[2])
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
		db, err := duckstore.OpenInitialized(ctx, os.Args[2])
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
		db, err := duckstore.OpenInitialized(ctx, os.Args[2])
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
		db, err := duckstore.OpenReadOnly(ctx, os.Args[2])
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
		if status.SchemaVersion != status.LatestSchemaVersion {
			fmt.Println(" (unsupported; preserve evidence and rebuild)")
			break
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
	switch {
	case errors.Is(err, errHelp):
		usage()
		os.Exit(0)
	case errors.Is(err, errUsage):
		fmt.Fprintln(os.Stderr, "error:", err)
		usage()
		os.Exit(2)
	default:
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}
