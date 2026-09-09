package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"io"
	"os"
	"path/filepath"
	"time"

	"github.com/yinhm/alphalake/internal/ingest"
	"github.com/yinhm/alphalake/internal/source/chinabond"
	"github.com/yinhm/alphalake/internal/source/damodaran"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

func runCountryRiskSync(ctx context.Context, args []string) error {
	return runReferenceSync(ctx, "sync-country-risk", args)
}
func runReferenceSync(ctx context.Context, command string, args []string) error {
	if len(args) < 1 {
		return errors.New("usage: " + command + " <db-path> [--offline] [--python executable] [--parser path]")
	}
	fs := flag.NewFlagSet(command, flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	python := fs.String("python", "python3", "Python with the selected parser dependencies")
	defaultScript := damodaran.DefaultScript
	switch command {
	case "sync-industry-beta":
		defaultScript = damodaran.BetaScript
	case "sync-cny-yield":
		defaultScript = chinabond.Script
	}
	script := fs.String("parser", defaultScript, "reviewed parser script")
	offline := fs.Bool("offline", false, "replay latest verified local archive without HTTP")
	if err := fs.Parse(args[1:]); err != nil {
		return err
	}
	if fs.NArg() != 0 {
		return errors.New("unexpected country-risk arguments")
	}
	db, err := duckstore.OpenAndMigrate(ctx, args[0])
	if err != nil {
		return err
	}
	defer db.Close()
	sync := ingest.SyncCountryRisk
	switch command {
	case "sync-industry-beta":
		sync = ingest.SyncIndustryBeta
	case "sync-cny-yield":
		sync = ingest.SyncCNYGovernmentYield
	}
	out, err := sync(ctx, db, filepath.Join(filepath.Dir(args[0]), "raw"), ingest.ReferenceOptions{Python: *python, Script: *script, Offline: *offline})
	if encodeErr := json.NewEncoder(os.Stdout).Encode(out); encodeErr != nil {
		return errors.Join(err, encodeErr)
	}
	return err
}

func runWACCReferenceExport(ctx context.Context, args []string) error {
	if len(args) < 1 {
		return errors.New("usage: export-wacc-references <db-path> --as-of RFC3339 --country-release N --beta-release N --yield-release N [--recorded-cutoff RFC3339]")
	}
	fs := flag.NewFlagSet("export-wacc-references", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	at := fs.String("as-of", "", "public information cutoff")
	recorded := fs.String("recorded-cutoff", "", "optional system knowledge cutoff")
	country := fs.Int64("country-release", 0, "explicit country release")
	beta := fs.Int64("beta-release", 0, "explicit beta release")
	yield := fs.Int64("yield-release", 0, "explicit yield release")
	if err := fs.Parse(args[1:]); err != nil {
		return err
	}
	if fs.NArg() != 0 {
		return errors.New("unexpected reference export arguments")
	}
	asof, err := time.Parse(time.RFC3339Nano, *at)
	if err != nil {
		return err
	}
	var cutoff *time.Time
	if *recorded != "" {
		r, err := time.Parse(time.RFC3339Nano, *recorded)
		if err != nil {
			return err
		}
		cutoff = &r
	}
	if _, err := os.Stat(args[0]); err != nil {
		return err
	}
	db, err := duckstore.Open(ctx, args[0])
	if err != nil {
		return err
	}
	defer db.Close()
	result, err := duckstore.ExportWACCReferences(ctx, db, asof, cutoff, *country, *beta, *yield)
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(result)
}
