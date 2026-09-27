package main

import (
	"crypto/sha256"
	"fmt"
	"os"
	"path/filepath"
	"testing"

	store "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestRequestsPreserveGapsAndExcludeOutOfScope(t *testing.T) {
	c := coverage{Contract: "alphalake-native-coverage-v1", Period: "2026-06-30", AsOf: "2026-09-27T08:12:14Z", Companies: []company{
		{ID: 1, Code: "600004", Scope: "nonfinancial_by_reference", Gaps: []gap{{"r_and_d_expense", "annual", "2020-12-31"}, {"r_and_d_expense", "annual", "2020-12-31"}, {"r_and_d_expense", "annual", "2021-12-31"}, {"r_and_d_expense", "quarterly", "2026-06-30"}}},
		{ID: 2, Code: "600005", Scope: "outside_financial_scope", Gaps: []gap{{"r_and_d_expense", "annual", "2020-12-31"}}},
	}}
	r, e := requests(c)
	if e != nil || len(r) != 2 || r[0].ID != 1 {
		t.Fatalf("requests=%v err=%v", r, e)
	}
	c.Companies[1].ID = 1
	if _, e = requests(c); e == nil {
		t.Fatal("duplicate identity accepted")
	}
	c.Companies[1].ID = 2
	c.AsOf = "bad"
	if _, e = requests(c); e == nil {
		t.Fatal("bad cutoff accepted")
	}
}

func TestArchiveEvidenceRejectsTampering(t *testing.T) {
	raw, e := os.ReadFile("../../internal/ingest/testdata/generic-valuation-2026/gpcw20250630.zip")
	if e != nil {
		t.Fatal(e)
	}
	hash := fmt.Sprintf("%x", sha256.Sum256(raw))
	root := t.TempDir()
	path := filepath.Join(root, "fixture.zip")
	if e = os.WriteFile(path, raw, 0600); e != nil {
		t.Fatal(e)
	}
	p, e := store.ReadFinancialArchive(root, "fixture.zip", "gpcw20250630.zip", hash, int64(len(raw)))
	if e != nil || len(p.Records) == 0 {
		t.Fatalf("records=%d err=%v", len(p.Records), e)
	}
	raw[len(raw)-1] ^= 1
	if e = os.WriteFile(path, raw, 0600); e != nil {
		t.Fatal(e)
	}
	if _, e = store.ReadFinancialArchive(root, "fixture.zip", "gpcw20250630.zip", hash, int64(len(raw))); e == nil {
		t.Fatal("tampered archive accepted")
	}
}
