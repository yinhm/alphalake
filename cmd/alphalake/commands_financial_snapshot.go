package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	store "github.com/yinhm/alphalake/internal/store/duckdb"
	"os"
	"strings"
	"time"
)

func runFinancialSnapshotExport(ctx context.Context, args []string) error {
	if len(args) < 1 {
		return usageError("usage: export-financial-snapshot <db-path> --output <new-directory> --fields <standard-names> --from YYYY-MM-DD --period YYYY-MM-DD --as-of RFC3339 [--codes 300866,600519] [--field-periods projection.json]")
	}
	fs := flag.NewFlagSet("export-financial-snapshot", flag.ContinueOnError)
	output := fs.String("output", "", "new temporary export directory")
	codes := fs.String("codes", "", "comma-separated security codes; empty selects Shanghai/Shenzhen")
	scopeFile := fs.String("scope-file", "", "JSON array of reviewed instrument_id/ticker/name publication identities; excludes --codes")
	fields := fs.String("fields", "", "standard fields")
	projection := fs.String("field-periods", "", "JSON file mapping each selected standard field to exact report dates")
	from := fs.String("from", "", "first report period")
	period := fs.String("period", "", "last report period")
	asof := fs.String("as-of", "", "information cutoff")
	if e := fs.Parse(args[1:]); e != nil {
		return parseError(e)
	}
	start, e := time.Parse("2006-01-02", *from)
	if e != nil {
		return parseError(e)
	}
	end, e := time.Parse("2006-01-02", *period)
	if e != nil {
		return parseError(e)
	}
	at, e := time.Parse(time.RFC3339Nano, *asof)
	if e != nil {
		return parseError(e)
	}
	var fieldPeriods map[string][]time.Time
	if *projection != "" {
		raw, err := os.ReadFile(*projection)
		if err != nil {
			return err
		}
		var values map[string][]string
		if err = json.Unmarshal(raw, &values); err != nil || len(values) == 0 {
			return fmt.Errorf("nonempty standard field/report period object required")
		}
		fieldPeriods = map[string][]time.Time{}
		for field, dates := range values {
			for _, value := range dates {
				day, err := time.Parse("2006-01-02", value)
				if err != nil {
					return err
				}
				fieldPeriods[field] = append(fieldPeriods[field], day)
			}
			if len(dates) == 0 {
				return fmt.Errorf("report dates required for %s", field)
			}
		}
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
	var scope []store.FinancialExportSecurity
	if *scopeFile != "" {
		raw, err := os.ReadFile(*scopeFile)
		if err != nil {
			return err
		}
		if err = json.Unmarshal(raw, &scope); err != nil || len(scope) == 0 {
			return fmt.Errorf("nonempty frozen publication scope required")
		}
	}
	return store.ExportFinancialSQLiteRows(ctx, db, *output, selected, strings.Split(*fields, ","), start, end, at, fieldPeriods, scope)
}
