package ingest

import (
	"bytes"
	"crypto/sha256"
	"encoding/csv"
	"fmt"
	"path/filepath"
	"strings"
	"testing"

	"github.com/yinhm/alphalake/internal/source/tdx/financial"
)

func TestRealEarningsWorkingCapitalValues(t *testing.T) {
	assertRealMappedFields(t, "testdata/earnings-working-capital-2026", 38, 7)
}

func TestRealCashRDValues(t *testing.T) {
	assertRealMappedFields(t, "testdata/cash-rd-2026", 33, 6)
}

func TestRealBalanceProfitValues(t *testing.T) {
	assertRealMappedFields(t, "testdata/balance-profit-2026", 41, 12)
}

func assertRealMappedFields(t *testing.T, dir string, count, fieldCount int) {
	t.Helper()
	rows, err := csv.NewReader(bytes.NewReader(readFinancialSample(t, dir, "values.csv"))).ReadAll()
	if err != nil || len(rows) != count+1 {
		t.Fatalf("evidence rows=%d error=%v", len(rows), err)
	}
	fields := map[string]bool{}
	for _, row := range rows[1:] {
		pdf := readFinancialSample(t, dir, row[7])
		if fmt.Sprintf("%x", sha256.Sum256(pdf)) != row[8] {
			t.Fatal("PDF hash", row[7])
		}
		name := "gpcw" + strings.ReplaceAll(row[2], "-", "") + ".zip"
		pkg, err := financial.ParsePackage(name, readFinancialSample(t, filepath.Join("testdata", "valuation-chain-2026"), name))
		if err != nil {
			t.Fatal(err)
		}
		assertReportSampleValues(t, pkg, [][]string{row})
		fields[row[1]] = true
	}
	if len(fields) != fieldCount {
		t.Fatal("field coverage", fields)
	}
}
