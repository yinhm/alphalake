package ingest

import (
	"bytes"
	"encoding/csv"
	"math"
	"math/big"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/yinhm/alphalake/internal/source/tdx/financial"
)

const annualSampleDir = "testdata/annual-2025"

const quarterSampleDir = "testdata/quarters-2025"

func readAnnualSample(t *testing.T, name string) []byte {
	t.Helper()
	return readFinancialSample(t, annualSampleDir, name)
}

func readFinancialSample(t *testing.T, dir, name string) []byte {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(dir, name))
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func annualReportValues(t *testing.T) [][]string {
	t.Helper()
	return financialSampleValues(t, annualSampleDir, "values.csv", 16)
}

func financialSampleValues(t *testing.T, dir, name string, want int) [][]string {
	t.Helper()
	rows, err := csv.NewReader(bytes.NewReader(readFinancialSample(t, dir, name))).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != want+1 || len(rows[0]) != 9 {
		t.Fatalf("expected header and %d report values, got %d rows", want, len(rows))
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
	assertReportSampleValues(t, pkg, annualReportValues(t))
}

func assertReportSampleValues(t *testing.T, pkg financial.Package, rows [][]string) {
	t.Helper()
	index := make(map[string]financial.Record)
	for _, record := range pkg.Records {
		index[record.Code] = record
	}
	for _, row := range rows {
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

func TestRealQuarterReportValues(t *testing.T) {
	values := financialSampleValues(t, quarterSampleDir, "values.csv", 24)
	cumulative := financialSampleValues(t, quarterSampleDir, "cumulative.csv", 24)
	totals := map[string]*big.Rat{}
	decimal := func(s string) *big.Rat {
		t.Helper()
		n, ok := new(big.Rat).SetString(s)
		if !ok {
			t.Fatalf("invalid report decimal %q", s)
		}
		return n
	}
	for i, row := range values {
		ytd := cumulative[i]
		if strings.Join(row[:3], "/") != strings.Join(ytd[:3], "/") || row[4] != "CNY" || ytd[4] != "CNY" {
			t.Fatalf("quarter/YTD identity or unit mismatch: %v / %v", row, ytd)
		}
		key := row[0] + "/" + row[1]
		if totals[key] == nil {
			totals[key] = new(big.Rat)
		}
		totals[key].Add(totals[key], decimal(row[5]))
		if totals[key].Cmp(decimal(ytd[5])) != 0 {
			t.Fatalf("%s/%s: annual-table quarterly sum differs from contemporary YTD %s", key, row[3], ytd[5])
		}
	}
	for q, period := range []string{"2025-03-31", "2025-06-30", "2025-09-30"} {
		t.Run(period, func(t *testing.T) {
			name := "gpcw" + strings.ReplaceAll(period, "-", "") + ".zip"
			pkg, err := financial.ParsePackage(name, readFinancialSample(t, quarterSampleDir, name))
			if err != nil {
				t.Fatal(err)
			}
			if len(pkg.Records) != 2 || pkg.Header.ReportSize != 584*4 {
				t.Fatalf("unexpected sample header: %+v", pkg.Header)
			}
			var selected [][]string
			for i, row := range values {
				if row[2] == period {
					if row[3] != []string{"Q1", "Q2", "Q3"}[q] || cumulative[i][3] != []string{"Q1", "H1", "9M"}[q] {
						t.Fatal("quarter/YTD period labels mismatch")
					}
					selected = append(selected, row)
				}
			}
			if len(selected) != 8 {
				t.Fatalf("expected eight values per quarter, got %d", len(selected))
			}
			assertReportSampleValues(t, pkg, selected)
		})
	}
}
