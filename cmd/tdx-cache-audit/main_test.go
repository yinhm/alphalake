package main

import (
	"crypto/md5"
	"fmt"
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
