package main

import (
	"context"
	"encoding/json"
	"flag"
	"os"
	"time"

	"github.com/yinhm/alphalake/internal/source/tdx/financial"
	duck "github.com/yinhm/alphalake/internal/store/duckdb"
)

func runSourceFinancial(ctx context.Context, args []string) error {
	encoder := json.NewEncoder(os.Stdout)
	if args[0] == "tdx-financial-fields" {
		if len(args) != 1 {
			return usageError("tdx-financial-fields takes no arguments")
		}
		fields, err := financial.FieldCatalog()
		if err != nil {
			return err
		}
		return encoder.Encode(struct {
			Version string                      `json:"catalog_version"`
			Fields  []financial.FieldDefinition `json:"fields"`
		}{financial.CatalogVersion, fields})
	}
	if len(args) < 3 {
		return usageError("usage: export-financial-source <db> <code> --period YYYY-MM-DD")
	}
	fs := flag.NewFlagSet(args[0], flag.ContinueOnError)
	period := fs.String("period", "", "report period")
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
	db, err := duck.OpenReadOnly(ctx, args[1])
	if err != nil {
		return err
	}
	defer db.Close()
	data, err := duck.ExportSourceFinancialData(ctx, db, args[2], end)
	if err != nil {
		return err
	}
	return encoder.Encode(data)
}
