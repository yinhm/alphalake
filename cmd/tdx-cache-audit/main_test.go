package main

import (
	"context"
	"crypto/md5"
	"fmt"
	store "github.com/yinhm/alphalake/internal/store/duckdb"
	"os"
	"path/filepath"
	"testing"
)

func TestRealPackageAuditAndTamper(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "tdx-cache")
	if err := os.Mkdir(dir, 0700); err != nil {
		t.Fatal(err)
	}
	name := "gpcw20251231.zip"
	raw, err := os.ReadFile(filepath.Join("..", "..", "internal", "ingest", "testdata", "anker-valuation-2026", name))
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(dir, name), raw, 0600); err != nil {
		t.Fatal(err)
	}
	manifest := fmt.Sprintf("%s,%x,%d\n", name, md5.Sum(raw), len(raw))
	if err = os.WriteFile(filepath.Join(dir, "gpcw.txt"), []byte(manifest), 0600); err != nil {
		t.Fatal(err)
	}
	requests := []request{{"300866", "2025-12-31", "research_and_development_expense"}, {"999999", "2025-12-31", "research_and_development_expense"}}
	result, err := audit(dir, requests, nil)
	if err != nil {
		t.Fatal(err)
	}
	rows := result["results"].([]map[string]any)
	if rows[0]["status"] != "source_matches_require_standard_chain_check" || rows[1]["status"] != "absent_code_in_verified_local_package" {
		t.Fatal(rows)
	}
	evidence := rows[0]["source_evidence"].([]map[string]any)
	if len(evidence) != 1 || evidence[0]["status"] != "source_nonzero_not_standard_approval" {
		t.Fatal(evidence)
	}
	raw[len(raw)-1] ^= 1
	if err = os.WriteFile(filepath.Join(dir, name), raw, 0600); err != nil {
		t.Fatal(err)
	}
	if _, err = audit(dir, requests, nil); err == nil {
		t.Fatal("corrupt archive accepted")
	}
	if err = os.Remove(filepath.Join(dir, name)); err != nil {
		t.Fatal(err)
	}
	result, err = audit(dir, requests, nil)
	if err != nil || result["results"].([]map[string]any)[0]["status"] != "missing_local_package" {
		t.Fatal(result, err)
	}
}

func TestStandardChainPreservesLatestRevisionAndRejectionReasons(t *testing.T) {
	db, err := store.Open(context.Background(), filepath.Join(t.TempDir(), "db.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	_, err = db.Exec(`CREATE SCHEMA fundamental; CREATE SCHEMA meta;
 CREATE TABLE fundamental.field(canonical_field VARCHAR,unit VARCHAR,value_kind VARCHAR,period_basis VARCHAR);
 INSERT INTO fundamental.field VALUES('revenue_cumulative','CNY','monetary','ytd');
 CREATE TABLE meta.artifact(artifact_id BIGINT,sha256 VARCHAR,fetched_at TIMESTAMP);
 INSERT INTO meta.artifact VALUES(1,'old','2026-01-01'),(2,'new','2026-02-01');
 CREATE TABLE fundamental.source_record(source_record_id BIGINT,artifact_id BIGINT,provider_code VARCHAR,report_period DATE,instrument_id BIGINT);
 INSERT INTO fundamental.source_record VALUES(1,1,'300001','2025-12-31',1),(2,2,'300001','2025-12-31',1),
 (3,2,'300002','2025-12-31',2),(4,2,'300003','2025-12-31',NULL),(5,2,'300004','2025-12-31',4);
 CREATE TABLE fundamental.statement_snapshot(source_record_id BIGINT,revenue_cumulative DECIMAL(38,10));
 INSERT INTO fundamental.statement_snapshot VALUES(1,123.5),(2,NULL),(3,0),(5,NULL);
 CREATE TABLE fundamental.statement_rejection(source_record_id BIGINT,rule_code VARCHAR,fields VARCHAR[]);
 INSERT INTO fundamental.statement_rejection VALUES(2,'provider_zero_ambiguous',['revenue_cumulative']),
 (4,'unresolved_provider_identity',['revenue_cumulative']);`)
	if err != nil {
		t.Fatal(err)
	}
	requests := []request{}
	for _, code := range []string{"300001", "300002", "300003", "300004", "300005"} {
		requests = append(requests, request{code, "2025-12-31", "revenue_cumulative"})
	}
	requests = append(requests, request{"300001", "2025-12-31", "not_installed"})
	result, err := standardChain(db, requests)
	if err != nil {
		t.Fatal(err)
	}
	states := map[string]string{}
	for _, r := range result {
		states[r["code"].(string)+"/"+r["field"].(string)] = r["status"].(string)
		if r["code"] == "300001" && r["field"] == "revenue_cumulative" && (r["value"] != nil || r["artifact_sha256"] != "new") {
			t.Fatal("old revision filled latest null", r)
		}
		if r["code"] == "300002" && r["value"] != "0.0000000000" {
			t.Fatal("true standard zero lost", r)
		}
	}
	for key, want := range map[string]string{"300001/revenue_cumulative": "rejected_source_zero", "300002/revenue_cumulative": "available_standard_fact",
		"300003/revenue_cumulative": "unresolved_source_identity", "300004/revenue_cumulative": "missing_standard_fact_without_rejection",
		"300005/revenue_cumulative": "no_source_record", "300001/not_installed": "standard_field_not_installed"} {
		if states[key] != want {
			t.Fatal(key, states, want)
		}
	}
}
