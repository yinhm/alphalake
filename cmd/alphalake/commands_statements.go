package main

import (
	"context"
	"encoding/json"
	"flag"
	"os"
	"time"

	duck "github.com/yinhm/alphalake/internal/store/duckdb"
)

func runFinancialStatements(ctx context.Context, args []string) error {
	if len(args) < 3 {
		return usageError("usage: financial-statements <db> <code> --period YYYY-MM-DD --as-of RFC3339 [--include-evidence]")
	}
	fs := flag.NewFlagSet(args[0], flag.ContinueOnError)
	period := fs.String("period", "", "report period (quarter end)")
	cutoff := fs.String("as-of", "", "information cutoff")
	evidence := fs.Bool("include-evidence", false, "include source lineage")
	if err := fs.Parse(args[3:]); err != nil {
		return parseError(err)
	}
	if fs.NArg() != 0 {
		return usageError("unexpected arguments")
	}
	end, err := time.Parse("2006-01-02", *period)
	if err != nil {
		return parseError(err)
	}
	asof, err := time.Parse(time.RFC3339, *cutoff)
	if err != nil {
		return parseError(err)
	}
	db, err := duck.OpenReadOnly(ctx, args[1])
	if err != nil {
		return err
	}
	defer db.Close()
	data, err := duck.ExportFinancialStatements(ctx, db, args[2], end, asof, *evidence)
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(data)
}
