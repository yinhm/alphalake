package ingest

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"math"
	"strconv"
	"strings"
	"testing"

	"github.com/yinhm/alphalake/internal/source/tdx/financial"
)

// 原始供应商记录→已归档标准输入的位核对；PDF语义由同目录verify.py另行核验。
func TestRealBSEValuationSourceInputs(t *testing.T) {
	const dir = "testdata/bse-valuation-2026"
	var packages []struct {
		File, Period string
		SHA          string `json:"sample_sha256"`
		Original     string `json:"original_archive_sha256"`
		Record       string `json:"record_sha256"`
		Bytes        int    `json:"record_bytes"`
	}
	if err := json.Unmarshal(readFinancialSample(t, dir, "packages.json"), &packages); err != nil {
		t.Fatal(err)
	}
	var facts []struct {
		Field, Period, Code, Unit, Value string
		Bits                             uint32
		Multiplier                       float64
		InstrumentID                     int64  `json:"instrument_id"`
		Archive                          string `json:"artifact_sha256"`
	}
	if err := json.Unmarshal(readFinancialSample(t, dir, "inputs.json"), &facts); err != nil {
		t.Fatal(err)
	}
	if len(packages) != 6 || len(facts) != 32 {
		t.Fatal("unexpected sample scope", len(packages), len(facts))
	}
	seen := map[string]bool{}
	for _, p := range packages {
		raw := readFinancialSample(t, dir, p.File)
		if fmt.Sprintf("%x", sha256.Sum256(raw)) != p.SHA {
			t.Fatal("slice hash", p.File)
		}
		pkg, err := financial.ParsePackage(p.File, raw)
		if err != nil {
			t.Fatal(err)
		}
		if len(pkg.Records) != 1 || pkg.Records[0].Code != "920000" || pkg.Records[0].ReportPeriod.Format("2006-01-02") != p.Period {
			t.Fatal("source identity/period", p.File)
		}
		record := pkg.Records[0]
		bytes := make([]byte, len(record.Fields)*4)
		for i, f := range record.Fields {
			binary.LittleEndian.PutUint32(bytes[i*4:], f.Bits)
		}
		if len(bytes) != p.Bytes || fmt.Sprintf("%x", sha256.Sum256(bytes)) != p.Record {
			t.Fatal("record differs from original full archive", p.File)
		}
		for _, f := range facts {
			if f.Period != p.Period {
				continue
			}
			n, err := strconv.Atoi(strings.TrimPrefix(f.Field, "FN"))
			if err != nil || n < 1 || n > len(record.Fields) {
				t.Fatal("field", f.Field, err)
			}
			key := f.Period + "/" + f.Field
			if seen[key] || f.Code != "920000" || f.InstrumentID != 52135 || f.Archive != p.Original || record.Fields[n-1].Bits != f.Bits {
				t.Fatal("standard input lineage/bits", key)
			}
			seen[key] = true
			unit := "CNY"
			if f.Field == "FN238" {
				unit = "share"
			}
			value, err := strconv.ParseFloat(f.Value, 64)
			if err != nil || f.Unit != unit || (f.Multiplier != 1 && f.Multiplier != 10000) || f.Value != fmt.Sprintf("%.10f", float64(math.Float32frombits(f.Bits))*f.Multiplier) {
				t.Fatal("normalization changed", key, value, err)
			}
		}
	}
	if len(seen) != 32 {
		t.Fatal("missing standard inputs", len(seen))
	}
}
