package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/yinhm/alphalake/internal/ingest"
	"github.com/yinhm/alphalake/internal/source/cninfo"
	"github.com/yinhm/alphalake/internal/source/eastmoney"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

func runEarningsGuidance(ctx context.Context, command string, args []string) error {
	if len(args) < 1 {
		return usageError("%s requires a database path", command)
	}
	if command == "upgrade-earnings-guidance" {
		if len(args) != 1 {
			return usageError("upgrade-earnings-guidance <unpublished-schema57-copy>")
		}
		db, err := duckstore.Open(ctx, args[0])
		if err != nil {
			return err
		}
		defer db.Close()
		return duckstore.UpgradeEarningsGuidance(ctx, db)
	}
	fs := flag.NewFlagSet(command, flag.ContinueOnError)
	period := fs.String("period", "", "guidance operating period end YYYY-MM-DD")
	start := fs.String("start", "", "CNINFO announcement window start YYYY-MM-DD")
	end := fs.String("end", "", "CNINFO announcement window end YYYY-MM-DD")
	scopeFile := fs.String("scope-file", "", "explicit classified nonfinancial scope JSON: {source,tickers}")
	root := fs.String("workspace", filepath.Dir(args[0]), "artifact workspace")
	offline := fs.Bool("offline", false, "replay complete archived request sets; never advance online version")
	metadataOnly := fs.Bool("metadata-only", false, "archive catalogue without downloading PDF attachments")
	if err := fs.Parse(args[1:]); err != nil {
		return err
	}
	if fs.NArg() != 0 || *period == "" || *scopeFile == "" {
		return usageError("%s <db> --period YYYY-MM-DD --scope-file file [--start YYYY-MM-DD --end YYYY-MM-DD --offline --metadata-only]", command)
	}
	lockPath := filepath.Join(*root, "derived", "earnings-guidance", "sync.lock")
	if err := os.MkdirAll(filepath.Dir(lockPath), 0700); err != nil {
		return err
	}
	lock, err := os.OpenFile(lockPath, os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return err
	}
	defer lock.Close()
	if err = syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		return fmt.Errorf("guidance workspace already locked: %w", err)
	}
	defer syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
	raw, err := os.ReadFile(*scopeFile)
	if err != nil {
		return err
	}
	var scope struct {
		Source  string   `json:"source"`
		Tickers []string `json:"tickers"`
	}
	decoder := json.NewDecoder(strings.NewReader(string(raw)))
	decoder.DisallowUnknownFields()
	if err = decoder.Decode(&scope); err != nil || scope.Source == "" || len(scope.Tickers) == 0 {
		return fmt.Errorf("nonempty scope source/tickers required: %v", err)
	}
	if command == "earnings-guidance" {
		db, err := duckstore.OpenReadOnly(ctx, args[0])
		if err != nil {
			return err
		}
		defer db.Close()
		version, err := duckstore.CurrentSchemaVersion(ctx, db)
		if err != nil || version != duckstore.SchemaVersion {
			return fmt.Errorf("guidance query requires current schema: %d (%v)", version, err)
		}
		if err = duckstore.ValidateGuidanceScope(ctx, db, scope.Tickers, time.Now().UTC()); err != nil {
			return err
		}
		rows, err := duckstore.QueryEarningsGuidance(ctx, db, *period, scope.Tickers)
		if err != nil {
			return err
		}
		observed := map[string]bool{}
		for _, row := range rows {
			ticker, _ := row["ticker"].(string)
			observed[ticker] = true
		}
		companies := []map[string]any{}
		for _, ticker := range scope.Tickers {
			status := "no_observation_stored"
			if observed[ticker] {
				status = "observed"
			}
			companies = append(companies, map[string]any{"ticker": ticker, "status": status})
		}
		return json.NewEncoder(os.Stdout).Encode(map[string]any{"contract_version": "earnings-guidance-context-v1", "period": *period, "scope_source": scope.Source, "scope_companies": len(scope.Tickers), "companies": companies, "observations": rows, "is_reported_actual": false, "growth_unit": "percent"})
	}
	if *start == "" || *end == "" {
		return usageError("explicit --start and --end required")
	}
	db, err := duckstore.OpenInitialized(ctx, args[0])
	if err != nil {
		return err
	}
	defer db.Close()
	ci, err := cninfo.NewDefaultClient()
	if err != nil {
		return err
	}
	result, err := ingest.SyncEarningsGuidance(ctx, db, *root, eastmoney.NewClient(), ci, ingest.GuidanceOptions{Period: *period, Start: *start, End: *end, Scope: scope.Tickers, Offline: *offline, MetadataOnly: *metadataOnly})
	return errors.Join(err, json.NewEncoder(os.Stdout).Encode(result))
}
