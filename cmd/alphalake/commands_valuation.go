package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"time"

	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

func runValuationExport(ctx context.Context, args []string) error {
	if len(args) < 2 {
		return errors.New("usage: export-valuation <db-path> <six-digit-code> --period YYYY-MM-DD --as-of RFC3339")
	}
	fs := flag.NewFlagSet("export-valuation", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	period := fs.String("period", "", "report period")
	asof := fs.String("as-of", "", "information cutoff")
	if err := fs.Parse(args[2:]); err != nil {
		return err
	}
	if fs.NArg() != 0 {
		return errors.New("unexpected export arguments")
	}
	end, err := time.Parse("2006-01-02", *period)
	if err != nil {
		return err
	}
	at, err := time.Parse(time.RFC3339, *asof)
	if err != nil {
		return err
	}
	if _, err := os.Stat(args[0]); err != nil {
		return err
	}
	db, err := duckstore.OpenReadOnly(ctx, args[0])
	if err != nil {
		return err
	}
	defer db.Close()
	result, err := duckstore.ExportValuationData(ctx, db, args[1], end, at)
	if err != nil {
		return err
	}
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	return encoder.Encode(result)
}

func runValuationReadiness(ctx context.Context, args []string) error {
	if len(args) < 1 {
		return errors.New("usage: valuation-readiness <db-path> --period YYYY-MM-DD --as-of RFC3339")
	}
	fs := flag.NewFlagSet("valuation-readiness", flag.ContinueOnError)
	code := fs.String("code", "", "optional six-digit security code; preserve ambiguous candidates")
	period := fs.String("period", "", "required quarter end")
	asof := fs.String("as-of", "", "information cutoff")
	if err := fs.Parse(args[1:]); err != nil {
		return err
	}
	if fs.NArg() != 0 {
		return errors.New("unexpected readiness arguments")
	}
	end, err := time.Parse("2006-01-02", *period)
	if err != nil {
		return err
	}
	at, err := time.Parse(time.RFC3339, *asof)
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
	var out map[string]any
	if *code == "" {
		out, err = duckstore.ExportValuationReadiness(ctx, db, end, at)
	} else {
		out, err = duckstore.ExportCompanyValuationReadiness(ctx, db, end, at, *code)
	}
	if err != nil {
		return err
	}
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	return encoder.Encode(out)
}

func runSupplementImport(ctx context.Context, args []string) error {
	if len(args) != 2 {
		return errors.New("usage: import-supplements <db-path> <reviewed-json-file>")
	}
	file, err := os.Open(args[1])
	if err != nil {
		return err
	}
	defer file.Close()
	var records []duckstore.ReviewedSupplement
	decoder := json.NewDecoder(file)
	decoder.DisallowUnknownFields()
	if err = decoder.Decode(&records); err != nil {
		return err
	}
	if err = decoder.Decode(new(any)); err != io.EOF {
		return errors.New("trailing supplement JSON")
	}
	if len(records) == 0 {
		return errors.New("empty supplement import")
	}
	db, err := duckstore.OpenAndMigrate(ctx, args[0])
	if err != nil {
		return err
	}
	defer db.Close()
	n, err := duckstore.ImportReviewedSupplements(ctx, db, records)
	if err != nil {
		return err
	}
	fmt.Printf("reviewed supplements: inserted=%d unchanged=%d\n", n, len(records)-n)
	return nil
}
