package ingest

import (
	"bytes"
	"crypto/sha256"
	"encoding/csv"
	"fmt"
	"strings"
	"testing"

	"github.com/yinhm/alphalake/internal/source/tdx/financial"
)

func taxDebtSampleValues(t *testing.T) [][]string {
	t.Helper()
	rows, err := csv.NewReader(bytes.NewReader(readFinancialSample(t, "testdata/tax-debt-2025", "values.csv"))).ReadAll()
	if err != nil || len(rows) != 17 || len(rows[0]) != 11 {
		t.Fatalf("tax/debt evidence: rows=%d error=%v", len(rows), err)
	}
	return rows[1:]
}

func TestRealTaxDebtValues(t *testing.T) {
	fields := map[string]bool{}
	for _, row := range taxDebtSampleValues(t) {
		dir := quarterSampleDir
		pdfDir := "testdata/core-financial-2025"
		if row[2] == "2025-12-31" {
			dir, pdfDir = "testdata/annual-2025", "testdata/tax-debt-2025"
		}
		name := "gpcw" + strings.ReplaceAll(row[2], "-", "") + ".zip"
		pkg, err := financial.ParsePackage(name, readFinancialSample(t, dir, name))
		if err != nil {
			t.Fatal(err)
		}
		id := strings.TrimSuffix(row[7][strings.LastIndex(row[7], "/")+1:], ".PDF")
		if fmt.Sprintf("%x", sha256.Sum256(readFinancialSample(t, pdfDir, id+".pdf"))) != row[8] {
			t.Fatalf("%s PDF hash mismatch", id)
		}
		fields[row[1]] = true
		row[5] = row[9] // 万元字段先核对源舍入后的编码，标准层另验乘数。
		assertReportSampleValues(t, pkg, [][]string{row})
	}
	if len(fields) != 9 {
		t.Fatalf("verified fields=%d, want 9", len(fields))
	}
}
