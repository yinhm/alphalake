package duckdb

import (
	"context"
	"encoding/json"
	"fmt"
	"path/filepath"
	"testing"
	"time"
)

func TestReadinessBatchesKeepFieldsAndLargeIDs(t *testing.T) {
	t.Setenv("ALPHALAKE_DUCKDB_MEMORY_LIMIT", "128MiB")
	t.Setenv("ALPHALAKE_DUCKDB_THREADS", "1")
	ctx := t.Context()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "batches.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	// 合成事实验证跨批次拼接、稀疏大ID及JSON精度；不作为真实来源证据。
	_, err = db.ExecContext(ctx, `INSERT INTO core.instrument(instrument_id,instrument_type,exchange_mic,currency,name)
 SELECT CASE WHEN i=129 THEN 9007199254740993 ELSE i END,'equity','XSHG','CNY','test-'||i FROM range(1,130) r(i);
 INSERT INTO core.instrument_identifier(instrument_id,provider,identifier_type,identifier_value)
 SELECT instrument_id,'tdx','symbol','sh'||CAST(600000+CAST(substr(name,6) AS INTEGER) AS VARCHAR) FROM core.instrument;
 INSERT INTO fundamental.fact(fact_id,instrument_id,canonical_field,report_period,announcement_time,period_type,
 statement_scope,currency,unit,value,primary_source,source_provider_field,provider_code,source_filing_id,revision_key,normalization_rule,materializer_version)
 SELECT 9007199254741100+CAST(substr(name,6) AS INTEGER),instrument_id,'monetary_funds','2026-06-30','2026-07-01','instant',
 'provider_default','CNY','CNY',CAST(substr(name,6) AS INTEGER),'tdx','FN8',CAST(600000+CAST(substr(name,6) AS INTEGER) AS VARCHAR),1,name,'test','test' FROM core.instrument`)
	if err != nil {
		t.Fatal(err)
	}
	result, err := ExportValuationReadiness(ctx, db, time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC), time.Date(2026, 9, 10, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	rows := result["companies"].([]map[string]any)
	if len(rows) != 129 {
		t.Fatal("lost company", len(rows))
	}
	for i, row := range rows {
		id := int64(i + 1)
		if i == 128 {
			id = 9007199254740993
		}
		if row["instrument_id"] != json.Number(fmt.Sprint(id)) {
			t.Fatal("identity/order changed", row["instrument_id"])
		}
		found := false
		for _, value := range row["fields"].([]any) {
			field := value.(map[string]any)
			if field["field"] != "FN8" {
				continue
			}
			found = true
			if field["status"] != "complete" || field["value"] != fmt.Sprintf("%d.0000000000", i+1) || field["source_fact_ids"].([]any)[0] != json.Number(fmt.Sprint(int64(9007199254741101)+int64(i))) {
				t.Fatal("field or lineage crossed company/batch", row)
			}
		}
		if !found {
			t.Fatal("lost window", id)
		}
	}
}

func TestValuationReadinessKeepsMissingCompanies(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "scan.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	_, err = db.ExecContext(ctx, `INSERT INTO core.instrument(instrument_id,instrument_type,exchange_mic,currency,name) VALUES
 (1,'equity','XSHG','CNY','无事实'),(2,'equity','XSHE','CNY','无代码'),(3,'etf','XSHG','CNY','基金'),(4,'equity','XSHG','USD','B股');
 INSERT INTO core.instrument_identifier(instrument_id,provider,identifier_type,identifier_value,valid_from) VALUES (1,'tdx','symbol','sh600001','2025-01-01');`)
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
	_, err = db.ExecContext(ctx, `INSERT INTO core.instrument(instrument_id,instrument_type,exchange_mic,currency) VALUES (1,'equity','XSHG','CNY');
 INSERT INTO core.instrument_identifier(instrument_id,provider,identifier_type,identifier_value) VALUES (1,'tdx','symbol','sh600001');
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

func TestCompanyReadinessPreservesCodeAmbiguity(t *testing.T) {
	ctx := t.Context()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "company.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	_, err = db.ExecContext(ctx, `INSERT INTO core.instrument(instrument_id,instrument_type,exchange_mic,currency,name) VALUES
 (1,'equity','XSHG','CNY','候选一'),(2,'equity','XSHE','CNY','候选二'),(3,'equity','XSHE','CNY','另一公司');
 INSERT INTO core.instrument_identifier(instrument_id,provider,identifier_type,identifier_value,valid_from) VALUES
 (1,'tdx','symbol','sh600001','2025-01-01'),(2,'tdx','symbol','sz600001','2025-01-01'),(3,'tdx','symbol','sz000001','2025-01-01');`)
	if err != nil {
		t.Fatal(err)
	}
	end := time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC)
	asof := end.AddDate(0, 3, 0)
	out, err := ExportCompanyValuationReadiness(ctx, db, end, asof, "600001")
	if err != nil {
		t.Fatal(err)
	}
	if out["universe_count"] != 2 || out["universe_scope"] != "local_security_code_candidates:600001" {
		t.Fatal(out)
	}
	counts := out["financial_status_counts"].(map[string]int)
	if counts["blocked_no_standard_facts"] != 2 {
		t.Fatal(counts)
	}
	out, err = ExportCompanyValuationReadiness(ctx, db, end, asof, "999999")
	if err != nil || out["universe_count"] != 0 {
		t.Fatal(out, err)
	}
	if _, err = ExportCompanyValuationReadiness(ctx, db, end, asof, "bad"); err == nil {
		t.Fatal("accepted invalid code")
	}
}
