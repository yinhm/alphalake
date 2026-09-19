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
	if named != 462 || counts["official"] != 461 || counts["reference"] != 1 || counts["unpublished"] != 122 {
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
	if fields[131].PeriodBasis != "opening_instant" {
		t.Fatal("opening balance treated as flow")
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

// The formula-system page complements the quant page; neither supersedes the
// other. Keep its repeated rows and shorter descriptions visible as evidence.
func TestOfficialProfinanceDirectory(t *testing.T) {
	fields, err := FieldCatalog()
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile("testdata/official-profinance-fields.html.gz")
	if err != nil {
		t.Fatal(err)
	}
	z, err := gzip.NewReader(bytes.NewReader(raw))
	if err != nil {
		t.Fatal(err)
	}
	defer z.Close()
	page, err := io.ReadAll(z)
	if err != nil {
		t.Fatal(err)
	}
	var receipt struct {
		SHA      string `json:"sha256"`
		Variants map[string]struct {
			Page    string `json:"page"`
			Catalog string `json:"catalog"`
		} `json:"label_variants"`
	}
	raw, err = os.ReadFile("testdata/official-profinance-fields.receipt.json")
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(raw, &receipt); err != nil {
		t.Fatal(err)
	}
	if fmt.Sprintf("%x", sha256.Sum256(page)) != receipt.SHA {
		t.Fatal("official page changed")
	}
	normalize := func(s string) string {
		return strings.Join(strings.Fields(strings.NewReplacer("：", ":", "（", "(", "）", ")").Replace(s)), "")
	}
	tags := regexp.MustCompile(`<[^>]+>`)
	cellsRE := regexp.MustCompile(`(?s)<td[^>]*>(.*?)</td>`)
	seen := map[int]int{}
	total := 0
	variants := 0
	for _, row := range regexp.MustCompile(`(?s)<tr[^>]*>(.*?)</tr>`).FindAllSubmatch(page, -1) {
		cells := cellsRE.FindAllSubmatch(row[1], -1)
		if len(cells) != 3 {
			continue
		}
		code := strings.TrimSpace(string(tags.ReplaceAll(cells[0][1], nil)))
		n, err := strconv.Atoi(code)
		if err != nil || n < 1 || n > len(fields) {
			t.Fatal("invalid source position", code)
		}
		label := strings.TrimSpace(string(tags.ReplaceAll(cells[2][1], nil)))
		f := fields[n-1]
		seen[n]++
		total++
		if v, ok := receipt.Variants[code]; ok {
			if label != v.Page || f.Label != v.Catalog {
				t.Fatal("description variant changed", code)
			}
			variants++
		} else if normalize(label) != normalize(f.Label) {
			t.Fatal("official definition conflicts", code, label, f.Label)
		}
		if n != 192 && (f.DefinitionStatus != "official" || !strings.Contains(f.Reference, "tdx_formula_fields_20260919")) {
			t.Fatal("official provenance missing", n)
		}
	}
	if total != 424 || len(seen) != 422 || seen[401] != 2 || seen[402] != 2 || variants != len(receipt.Variants) {
		t.Fatal(total, len(seen), variants)
	}
	for n, count := range seen {
		if n != 401 && n != 402 && count != 1 {
			t.Fatal("unexpected repeated position", n)
		}
	}
	if fields[164].DefinitionStatus != "reference" || fields[191].DefinitionStatus != "unpublished" || fields[583].DefinitionStatus != "official" {
		t.Fatal("missing page rows overwrote other evidence")
	}
}

func TestOfficialStatementCoverageAndUnits(t *testing.T) {
	fields, err := FieldCatalog()
	if err != nil {
		t.Fatal(err)
	}
	positions, approved, documented := 0, 0, 0
	for _, f := range fields {
		if f.ReviewReason == "" {
			t.Fatal("missing review disposition", f.Index)
		}
		if f.Statement == "" {
			continue
		}
		positions++
		if f.MappingStatus != "official_mapping" && f.MappingStatus != "reviewed_mapping" {
			continue
		}
		approved++
		if f.MappingStatus == "official_mapping" {
			documented++
		}
		if f.DefinitionStatus != "official" || f.Multiplier == nil || f.RequiresDisambiguation {
			t.Fatal("unsupported official mapping", f)
		}
		// The frozen official descriptions specify exceptional ten-thousand-yuan
		// encoding. The archived unit rules specify yuan for other amounts.
		scale := float64(1)
		if strings.Contains(f.Label, "万元") {
			scale = 10000
		}
		if *f.Multiplier != scale {
			t.Fatal("official source unit differs", f)
		}
		if (f.ValueKind == "monetary" && f.Unit != "CNY") || (f.ValueKind == "per_share" && f.Unit != "CNY/share") {
			t.Fatal("nonstandard statement unit", f)
		}
	}
	if positions != 283 || approved != 280 || documented != 124 {
		t.Fatal(positions, approved, documented)
	}
	for _, index := range []int{132, 155, 157} {
		if fields[index-1].PeriodBasis != "opening_instant" {
			t.Fatal("opening balance at closing date", index)
		}
	}
	if fields[70].ReviewReason != "period_requires_review" || fields[439].ReviewReason != "ambiguous_source_variant" || fields[452].ReviewReason != "ambiguous_source_variant" {
		t.Fatal("unreviewed statement positions lost")
	}
}
