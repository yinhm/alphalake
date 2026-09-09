package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
	"io"
	"os"
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
	db, err := duckstore.Open(ctx, args[0])
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
