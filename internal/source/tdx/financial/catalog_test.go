package financial

import (
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"os"
	"regexp"
	"strconv"
	"strings"
	"testing"

	"github.com/yinhm/alphalake/internal/domain"
)

func TestCompleteCatalogAndFrozenOfficialDefinitions(t *testing.T) {
	fields, err := FieldCatalog()
	if err != nil {
		t.Fatal(err)
	}
	if len(fields) != 584 {
		t.Fatal(len(fields))
	}
	counts := map[string]int{}
	named := 0
	for _, f := range fields {
		counts[f.DefinitionStatus]++
		if f.Name != "" {
			named++
		}
	}
	if named != 462 || counts["official"] != 437 || counts["reference"] != 25 || counts["unpublished"] != 122 {
		t.Fatal(named, counts)
	}
	raw, err := os.ReadFile("testdata/official-financial-fields.html.gz")
	if err != nil {
		t.Fatal(err)
	}
	zr, err := gzip.NewReader(bytes.NewReader(raw))
	if err != nil {
		t.Fatal(err)
	}
	page, err := io.ReadAll(zr)
	if err != nil {
		t.Fatal(err)
	}
	zr.Close()
	var receipt struct {
		SHA string `json:"sha256"`
	}
	r, err := os.ReadFile("testdata/official-financial-fields.receipt.json")
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(r, &receipt); err != nil {
		t.Fatal(err)
	}
	if fmt.Sprintf("%x", sha256.Sum256(page)) != receipt.SHA {
		t.Fatal("official evidence hash mismatch")
	}
	// Independently read the official HTML rows: every published slot must occur
	// in the complete dictionary, with its exact provider description retained.
	rows := regexp.MustCompile(`(?s)<tr[^>]*>(.*?)</tr>`).FindAllSubmatch(page, -1)
	tags := regexp.MustCompile(`<[^>]+>`)
	seen := 0
	for _, row := range rows {
		cells := regexp.MustCompile(`(?s)<td[^>]*>(.*?)</td>`).FindAllSubmatch(row[1], -1)
		if len(cells) != 4 {
			continue
		}
		code := string(tags.ReplaceAll(cells[0][1], nil))
		if !strings.HasPrefix(code, "FN") {
			continue
		}
		n, e := strconv.Atoi(code[2:])
		if e != nil {
			continue
		}
		seen++
		label := strings.TrimSpace(string(tags.ReplaceAll(cells[3][1], nil)))
		if fields[n-1].Label != label {
			t.Fatalf("source index %d label differs: %q / %q", n, fields[n-1].Label, label)
		}
	}
	if seen != 438 {
		t.Fatal(seen)
	}
	if fields[438].Name != "lease_liabilities" || *fields[438].Multiplier != 10000 || fields[580].Name != "right_of_use_depreciation" || fields[583].Name != "bond_issuance_cash_paid" {
		t.Fatal("late fields lost")
	}
}

func TestNamedSourceDecodingRejectsAmbiguityAndPreservesBits(t *testing.T) {
	fields, err := FieldCatalog()
	if err != nil {
		t.Fatal(err)
	}
	encode := func(v float32) domain.ProviderFloat32 {
		return domain.ProviderFloat32{Bits: math.Float32bits(v), Value: float64(v)}
	}
	v := DecodeSourceValue(fields[438], encode(309.80))
	if v.Value == nil || *v.Value != float64(float32(309.80))*10000 {
		t.Fatal(v)
	}
	for _, tc := range []struct {
		f     FieldDefinition
		v     domain.ProviderFloat32
		state string
	}{
		{fields[439], encode(1), "ambiguous_source_variant"},
		{fields[452], encode(1), "ambiguous_source_variant"},
		{fields[438], encode(0), "zero_requires_review"},
		{fields[438], encode(float32(math.NaN())), "nonfinite_source"},
		{fields[438], domain.ProviderFloat32{Bits: math.Float32bits(1), Value: 2}, "source_bits_mismatch"},
		{fields[191], encode(1), "undefined_source_field"},
		{fields[158], encode(1), "unit_requires_review"},
		{fields[313], encode(260828), "source_date_encoding_only"},
		{FieldDefinition{Index: 585}, encode(3), "undefined_source_field"},
	} {
		got := DecodeSourceValue(tc.f, tc.v)
		if got.State != tc.state || got.Value != nil || (got.Evidence.Bits == nil || *got.Evidence.Bits != tc.v.Bits) {
			t.Fatal(got, tc.state)
		}
	}
	if got := DecodeSourceValue(fields[335], encode(0)); got.Value == nil || *got.Value != 0 {
		t.Fatal("documented audit code zero lost", got)
	}
}

func TestRealPackageAllFieldsReachNamedDecoding(t *testing.T) {
	raw, err := os.ReadFile("../../../ingest/testdata/valuation-chain-2026/gpcw20260630.zip")
	if err != nil {
		t.Fatal(err)
	}
	pkg, err := ParsePackage("gpcw20260630.zip", raw)
	if err != nil {
		t.Fatal(err)
	}
	fields, err := FieldCatalog()
	if err != nil {
		t.Fatal(err)
	}
	if pkg.Header.ReportSize != 584*4 || len(pkg.Records) == 0 {
		t.Fatal(pkg.Header)
	}
	count := 0
	for _, record := range pkg.Records {
		if len(record.Fields) != len(fields) {
			t.Fatal(len(record.Fields))
		}
		for i, v := range record.Fields {
			parsed := DecodeSourceValue(fields[i], v)
			if (parsed.Evidence.Bits == nil || *parsed.Evidence.Bits != v.Bits) || parsed.Field != fields[i].Name || parsed.State == "" {
				t.Fatal(i, parsed)
			}
			if parsed.Value != nil && *parsed.Value != v.Value**fields[i].Multiplier {
				t.Fatal("unit conversion", i)
			}
			count++
		}
	}
	if count != len(pkg.Records)*584 {
		t.Fatal(count)
	}
}

func TestCatalogUnitsAndFrozenSupportingEvidence(t *testing.T) {
	fields, err := FieldCatalog()
	if err != nil {
		t.Fatal(err)
	}
	for _, f := range fields {
		if f.DefinitionStatus == "unpublished" && (f.Name != "" || f.Multiplier != nil || f.Unit != "unspecified") {
			t.Fatal("invented unpublished semantics", f)
		}
		if strings.Contains(f.Label, "%") && (f.Unit != "percent" || f.ValueKind != "ratio") {
			t.Fatal("percentage misclassified", f)
		}
		if strings.Contains(f.Label, "万元") && (f.Unit != "CNY" || f.Multiplier == nil || *f.Multiplier != 10000) {
			t.Fatal("wan yuan scale lost", f)
		}
		if strings.Contains(f.Label, "万股") && (f.Unit != "share" || f.Multiplier == nil || *f.Multiplier != 10000) {
			t.Fatal("wan shares scale lost", f)
		}
		if f.ValueKind == "shares" && f.Unit != "share" {
			t.Fatal("share unit differs from standard catalog", f)
		}
	}
	if fields[280].Unit != "unspecified" || fields[189].Unit != "percent" || fields[243].Unit != "share" {
		t.Fatal("ambiguous labels misclassified")
	}
	data, err := os.ReadFile("testdata/official-financial-fields.receipt.json")
	if err != nil {
		t.Fatal(err)
	}
	var receipt map[string]json.RawMessage
	if err = json.Unmarshal(data, &receipt); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"official-financial-unit-rules.html", "portfolio-types.py.txt"} {
		var want struct {
			SHA   string `json:"sha256"`
			Bytes int    `json:"bytes"`
		}
		if err = json.Unmarshal(receipt[name], &want); err != nil {
			t.Fatal(err)
		}
		packed, err := os.ReadFile("testdata/" + name + ".gz")
		if err != nil {
			t.Fatal(err)
		}
		zr, err := gzip.NewReader(bytes.NewReader(packed))
		if err != nil {
			t.Fatal(err)
		}
		raw, err := io.ReadAll(zr)
		if err != nil {
			t.Fatal(err)
		}
		zr.Close()
		if len(raw) != want.Bytes || fmt.Sprintf("%x", sha256.Sum256(raw)) != want.SHA {
			t.Fatal("supporting evidence changed", name)
		}
	}
}
