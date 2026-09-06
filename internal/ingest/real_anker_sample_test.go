package ingest

import (
	"bytes"
	"crypto/sha256"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"path/filepath"
	"strings"
	"testing"

	"github.com/yinhm/alphalake/internal/source/tdx/financial"
)

func TestRealAnkerValuationSourceValues(t *testing.T) {
	const dir = "testdata/anker-valuation-2026"
	rows, err := csv.NewReader(bytes.NewReader(readFinancialSample(t, dir, "values.csv"))).ReadAll()
	if err != nil || len(rows) != 85 || len(rows[0]) != 11 {
		t.Fatalf("Anker evidence: rows=%d error=%v", len(rows), err)
	}
	var packages []struct {
		File string
		SHA  string `json:"sample_sha256"`
	}
	if err := json.Unmarshal(readFinancialSample(t, dir, "packages.json"), &packages); err != nil {
		t.Fatal(err)
	}
	if len(packages) != 3 {
		t.Fatal("expected three original source periods")
	}
	count := 0
	for _, p := range packages {
		raw := readFinancialSample(t, dir, p.File)
		if fmt.Sprintf("%x", sha256.Sum256(raw)) != p.SHA {
			t.Fatal("source slice hash mismatch", p.File)
		}
		pkg, err := financial.ParsePackage(p.File, raw)
		if err != nil {
			t.Fatal(err)
		}
		if len(pkg.Records) != 1 || pkg.Records[0].Code != "300866" {
			t.Fatal("unexpected sample identity")
		}
		fields := map[string]bool{}
		for _, row := range rows[1:] {
			if "gpcw"+strings.ReplaceAll(row[2], "-", "")+".zip" != p.File {
				continue
			}
			id := strings.TrimSuffix(filepath.Base(row[7]), ".PDF")
			if fmt.Sprintf("%x", sha256.Sum256(readFinancialSample(t, dir, id+".pdf"))) != row[8] {
				t.Fatal("PDF hash mismatch", id)
			}
			fields[row[1]] = true
			row[5] = row[9] // 租赁负债和使用权折旧按万元编码核对，估值表保留 PDF 分位金额。
			assertReportSampleValues(t, pkg, [][]string{row})
			count++
		}
		if len(fields) != 28 {
			t.Fatalf("%s fields=%d, want 28", p.File, len(fields))
		}
	}
	if count != 84 {
		t.Fatalf("verified values=%d, want 84", count)
	}
}
