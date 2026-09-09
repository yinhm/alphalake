package duckdb

import (
	"context"
	"path/filepath"
	"testing"
	"time"
)

func TestValuationReadinessKeepsMissingCompanies(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "scan.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	_, err = db.ExecContext(ctx, `INSERT INTO ref.instrument(instrument_id,instrument_type,exchange_mic,currency,name) VALUES
 (1,'equity','XSHG','CNY','无事实'),(2,'equity','XSHE','CNY','无代码'),(3,'etf','XSHG','CNY','基金'),(4,'equity','XSHG','USD','B股');
 INSERT INTO ref.instrument_identifier(instrument_id,provider,identifier_type,identifier_value,valid_from) VALUES (1,'tdx','symbol','sh600001','2025-01-01');`)
	if err != nil {
		t.Fatal(err)
	}
	end := time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC)
	asof := end.AddDate(0, 3, 0)
	result, err := ExportValuationReadiness(ctx, db, end, asof)
	if err != nil {
		t.Fatal(err)
	}
	if result["universe_count"] != 2 {
		t.Fatalf("denominator: %v", result)
	}
	counts := result["financial_status_counts"].(map[string]int)
	if counts["blocked_no_standard_facts"] != 1 || counts["blocked_security_identity"] != 1 {
		t.Fatal(counts)
	}
	for _, row := range result["companies"].([]map[string]any) {
		if len(row["missing_core_fields"].([]string)) != 15 {
			t.Fatal(row)
		}
	}
	if _, err = ExportValuationReadiness(ctx, db, end.AddDate(0, 0, 1), asof); err == nil {
		t.Fatal("accepted non-quarter end")
	}
	if _, err = ExportValuationReadiness(ctx, db, end, end.AddDate(0, 0, -1)); err == nil {
		t.Fatal("accepted future period")
	}
}
