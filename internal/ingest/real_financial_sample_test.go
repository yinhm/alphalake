package ingest

import (
	"bytes"
	"encoding/csv"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"testing"

	"github.com/yinhm/alphalake/internal/source/tdx/financial"
)

const annualSampleDir = "testdata/annual-2025"

func readAnnualSample(t *testing.T, name string) []byte {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(annualSampleDir, name))
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func annualReportValues(t *testing.T) [][]string {
	t.Helper()
	rows, err := csv.NewReader(bytes.NewReader(readAnnualSample(t, "values.csv"))).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 17 || len(rows[0]) != 9 {
		t.Fatalf("expected header and 16 report values, got %d rows", len(rows))
	}
	return rows[1:]
}

// Values were transcribed from four CNINFO annual reports, independently of TDX.
// The ZIP contains six real records; only the container count/offsets changed.
func TestRealAnnualReportValues(t *testing.T) {
	pkg, err := financial.ParsePackage("gpcw20251231.zip", readAnnualSample(t, "gpcw20251231.zip"))
	if err != nil {
		t.Fatal(err)
	}
	if len(pkg.Records) != 6 || pkg.Header.ReportSize != 584*4 {
		t.Fatalf("unexpected sample header: %+v", pkg.Header)
	}
	index := make(map[string]financial.Record)
	for _, record := range pkg.Records {
		index[record.Code] = record
	}
	for _, row := range annualReportValues(t) {
		code, field := row[0], row[1]
		t.Run(code+"/"+field, func(t *testing.T) {
			record, ok := index[code]
			if !ok || record.ReportPeriod.Format("2006-01-02") != row[2] {
				t.Fatal("missing record or report period mismatch")
			}
			fieldNumber, err := strconv.Atoi(field[2:])
			if err != nil || fieldNumber < 1 || fieldNumber > len(record.Fields) {
				t.Fatalf("invalid field %q", field)
			}
			// Parse directly to float32 precision; the PDF's cents are not recoverable from TDX.
			printed, err := strconv.ParseFloat(row[5], 32)
			if err != nil {
				t.Fatal(err)
			}
			want := math.Float32bits(float32(printed))
			if got := record.Fields[fieldNumber-1].Bits; got != want {
				t.Fatalf("TDX bits=%08x PDF bits=%08x (%s yuan, %s page %s)", got, want, row[5], row[7], row[6])
			}
		})
	}
}
