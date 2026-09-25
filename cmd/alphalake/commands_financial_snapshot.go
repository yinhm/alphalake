package main

import (
	"context"
	"flag"
	"fmt"
	store "github.com/yinhm/alphalake/internal/store/duckdb"
	"strings"
	"time"
)

func runFinancialSnapshotExport(ctx context.Context, args []string) error {
	if len(args) < 1 {
		return fmt.Errorf("database path required")
	}
	fs := flag.NewFlagSet("export-financial-snapshot", flag.ContinueOnError)
	output := fs.String("output", "", "new temporary export directory")
	codes := fs.String("codes", "", "comma-separated security codes; empty selects Shanghai/Shenzhen")
	fields := fs.String("fields", "", "standard fields")
	from := fs.String("from", "", "first report period")
	period := fs.String("period", "", "last report period")
	asof := fs.String("as-of", "", "information cutoff")
	if e := fs.Parse(args[1:]); e != nil {
		return e
	}
	start, e := time.Parse("2006-01-02", *from)
	if e != nil {
		return e
	}
	end, e := time.Parse("2006-01-02", *period)
	if e != nil {
		return e
	}
	at, e := time.Parse(time.RFC3339Nano, *asof)
	if e != nil {
		return e
	}
	db, e := store.OpenReadOnly(ctx, args[0])
	if e != nil {
		return e
	}
	defer db.Close()
	db.SetMaxOpenConns(1)
	var selected []string
	if *codes != "" {
		selected = strings.Split(*codes, ",")
	}
	return store.ExportFinancialSQLiteRows(ctx, db, *output, selected, strings.Split(*fields, ","), start, end, at)
}
