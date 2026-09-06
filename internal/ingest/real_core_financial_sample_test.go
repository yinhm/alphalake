package ingest

import (
	"crypto/sha256"
	"fmt"
	"strings"
	"testing"

	"github.com/yinhm/alphalake/internal/source/tdx/financial"
)

func TestRealCoreFinancialValues(t *testing.T) {
	const dir = "testdata/core-financial-2025"
	rows := financialSampleValues(t, dir, "values.csv", 35)
	fields := map[string]bool{}
	for _, period := range []string{"2025-06-30", "2025-09-30"} {
		name := "gpcw" + strings.ReplaceAll(period, "-", "") + ".zip"
		pkg, err := financial.ParsePackage(name, readFinancialSample(t, quarterSampleDir, name))
		if err != nil {
			t.Fatal(err)
		}
		var selected [][]string
		for _, row := range rows {
			if row[2] != period {
				continue
			}
			selected = append(selected, row)
			fields[row[1]] = true
			id := strings.TrimSuffix(row[7][strings.LastIndex(row[7], "/")+1:], ".PDF")
			raw := readFinancialSample(t, dir, id+".pdf")
			if fmt.Sprintf("%x", sha256.Sum256(raw)) != row[8] {
				t.Fatalf("%s original PDF hash mismatch", id)
			}
		}
		assertReportSampleValues(t, pkg, selected)
	}
	if len(fields) != 20 {
		t.Fatalf("verified %d fields, want 20", len(fields))
	}
}
