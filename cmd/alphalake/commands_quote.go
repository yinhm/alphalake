package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"github.com/yinhm/alphalake/internal/ingest"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
	"io"
	"os"
	"path/filepath"
	"time"
)

func runValuationQuote(ctx context.Context, args []string) error {
	if len(args) < 2 {
		return errors.New("usage: export-valuation-quote <db> <tdx-symbol> --date YYYY-MM-DD --as-of RFC3339")
	}
	fs := flag.NewFlagSet("export-valuation-quote", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	dateText := fs.String("date", "", "exact market date")
	atText := fs.String("as-of", "", "information cutoff")
	if err := fs.Parse(args[2:]); err != nil {
		return err
	}
	if fs.NArg() != 0 {
		return errors.New("unexpected arguments")
	}
	day, err := time.Parse("2006-01-02", *dateText)
	if err != nil {
		return err
	}
	at, err := time.Parse(time.RFC3339Nano, *atText)
	if err != nil {
		return err
	}
	if _, err = os.Stat(args[0]); err != nil {
		return err
	}
	db, err := duckstore.OpenReadOnly(ctx, args[0])
	if err != nil {
		return err
	}
	defer db.Close()
	result, err := duckstore.ExportValuationQuote(ctx, db, args[1], day, at)
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(result)
}

func runMarketSource(ctx context.Context, command string, args []string) error {
	if len(args) < 2 {
		return errors.New("usage: " + command + " <db> <code-or-date> [--python path] [--offline]")
	}
	fs := flag.NewFlagSet(command, flag.ContinueOnError)
	python := fs.String("python", "python3", "parser Python")
	offline := fs.Bool("offline", false, "verified archive replay")
	if e := fs.Parse(args[2:]); e != nil {
		return e
	}
	if fs.NArg() != 0 {
		return errors.New("unexpected arguments")
	}
	db, e := duckstore.OpenAndMigrate(ctx, args[0])
	if e != nil {
		return e
	}
	defer db.Close()
	sync := ingest.SyncShareClasses
	if command == "sync-hkex-close" {
		sync = ingest.SyncHKEXQuote
	}
	if command == "sync-equity-proceeds" {
		sync = ingest.SyncEquityProceeds
	}
	out, e := sync(ctx, db, filepath.Join(filepath.Dir(args[0]), "raw"), args[1], ingest.ReferenceOptions{Python: *python, Offline: *offline})
	return errors.Join(e, json.NewEncoder(os.Stdout).Encode(out))
}

func runMarketCapitalExport(ctx context.Context, args []string) error {
	if len(args) < 2 {
		return errors.New("usage: export-market-capital <db> <code> --date YYYY-MM-DD --as-of RFC3339 --share-release N [--hk-release N --fx-release N]")
	}
	fs := flag.NewFlagSet("export-market-capital", flag.ContinueOnError)
	dayText := fs.String("date", "", "market date")
	atText := fs.String("as-of", "", "information cutoff")
	shares := fs.Int64("share-release", 0, "share release")
	hk := fs.Int64("hk-release", 0, "H price release")
	fx := fs.Int64("fx-release", 0, "FX release")
	ipo := fs.Int64("ipo-release", 0, "initial H offering proceeds release")
	greenshoe := fs.Int64("greenshoe-release", 0, "over-allotment proceeds release")
	if e := fs.Parse(args[2:]); e != nil {
		return e
	}
	if fs.NArg() != 0 {
		return errors.New("unexpected arguments")
	}
	day, e := time.Parse("2006-01-02", *dayText)
	if e != nil {
		return e
	}
	at, e := time.Parse(time.RFC3339Nano, *atText)
	if e != nil {
		return e
	}
	if _, e = os.Stat(args[0]); e != nil {
		return e
	}
	db, e := duckstore.OpenReadOnly(ctx, args[0])
	if e != nil {
		return e
	}
	defer db.Close()
	var funding []int64
	if *ipo != 0 || *greenshoe != 0 {
		funding = []int64{*ipo, *greenshoe}
	}
	out, e := duckstore.ExportMarketCapital(ctx, db, args[1], day, at, *shares, *hk, *fx, funding...)
	if e != nil {
		return e
	}
	return json.NewEncoder(os.Stdout).Encode(out)
}
