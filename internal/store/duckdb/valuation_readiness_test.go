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

func TestReadinessIndustryRequiresPublishedObservation(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "industry.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	_, err = db.ExecContext(ctx, `INSERT INTO ref.instrument(instrument_id,instrument_type,exchange_mic,currency) VALUES (1,'equity','XSHG','CNY');
 INSERT INTO ref.instrument_identifier(instrument_id,provider,identifier_type,identifier_value) VALUES (1,'tdx','symbol','sh600001');
 INSERT INTO classification.taxonomy VALUES (1,'tdx','tdx_industry','industry','industry');
 INSERT INTO classification.node(node_id,taxonomy_id,source_node_code,name) VALUES (1,1,'T010101','test');
 INSERT INTO meta.ingest_run(ingest_run_id,source,dataset,started_at,finished_at,status) VALUES (1,'tdx','classification_industry','2026-09-09 10:00:00+00','2026-09-09 11:00:00+00','completed');
 INSERT INTO classification.membership(instrument_id,node_id,effective_from,source,observed_at,ingest_run_id,last_observed_at,last_observed_run_id) VALUES (1,1,'2026-09-09','tdx','2026-09-09 10:00:00+00',1,'2026-09-09 10:00:00+00',1);`)
	if err != nil {
		t.Fatal(err)
	}
	end := time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC)
	for _, hour := range []int{10, 11} {
		r, e := ExportValuationReadiness(ctx, db, end, time.Date(2026, 9, 9, hour, 0, 0, 0, time.UTC))
		if e != nil {
			t.Fatal(e)
		}
		member := r["companies"].([]map[string]any)[0]["industry_memberships"]
		if hour == 10 && member != nil {
			t.Fatal("unpublished classification leaked")
		}
		if hour == 11 && len(member.([]any)) != 1 {
			t.Fatal("published classification missing")
		}
	}
}
