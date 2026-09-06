package ingest

import (
	"bytes"
	"crypto/sha256"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"math"
	"path/filepath"
	"strconv"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/artifact"
	"github.com/yinhm/alphalake/internal/domain"
	"github.com/yinhm/alphalake/internal/source/cninfo"
	"github.com/yinhm/alphalake/internal/source/tdx"
	"github.com/yinhm/alphalake/internal/source/tdx/financial"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

// Both report PDFs exist, but only the corrected TDX version was observed.
// Never synthesize an old provider record or backdate the current bytes.
func TestRealCorrectionWithoutOriginalProviderVersion(t *testing.T) {
	const dir = "testdata/correction-600113"
	ctx := t.Context()
	check := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	type document struct {
		ID, Value, SHA256 string
		Size              int
	}
	var evidence struct {
		Original, Corrected, Notice document
		Provider                    struct {
			FetchedAt time.Time `json:"fetched_at"`
		}
	}
	check(json.Unmarshal(readFinancialSample(t, dir, "evidence.json"), &evidence))
	for _, doc := range []document{evidence.Original, evidence.Corrected, evidence.Notice} {
		raw := readFinancialSample(t, dir, doc.ID+".pdf")
		if len(raw) != doc.Size || fmt.Sprintf("%x", sha256.Sum256(raw)) != doc.SHA256 {
			t.Fatalf("%s PDF evidence changed", doc.ID)
		}
	}
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "correction.duckdb"))
	check(err)
	defer db.Close()
	root := filepath.Join(t.TempDir(), "raw")
	id, err := duckstore.UpsertInstrument(ctx, db,
		domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHG", Currency: "CNY", Name: "浙江东日"},
		domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600113"})
	check(err)
	runID, err := duckstore.StartIngestRun(ctx, db, "tdx", "acceptance_sample", nil)
	check(err)
	rawCatalogue := readFinancialSample(t, dir, "catalogue.json")
	catalogue, err := artifact.Persist(ctx, db, root, artifact.Input{
		Source: "cninfo", Dataset: "filing_catalogue", SourceLocator: "sample/600113-correction.json",
		FetchedAt: time.Now(), MediaType: "application/json", ParserVersion: cninfo.CatalogueParserVersion, Content: rawCatalogue,
	})
	check(err)
	page, err := cninfo.ParseCataloguePage(rawCatalogue)
	check(err)
	if len(page.Filings) != 3 || len(page.Issues) != 0 {
		t.Fatalf("correction catalogue: %+v", page)
	}
	for i := range page.Filings {
		page.Filings[i].CatalogueArtifactID = catalogue.ArtifactID
	}
	_, err = duckstore.UpsertFilings(ctx, db, runID, page.Filings)
	check(err)
	raw := readFinancialSample(t, dir, "gpcw20250930.zip")
	stored, err := artifact.Persist(ctx, db, root, artifact.Input{
		Source: "tdx", Dataset: "acceptance_sample", SourceLocator: "sample/gpcw20250930.zip",
		FetchedAt: evidence.Provider.FetchedAt, MediaType: "application/zip", ParserVersion: "gpcw-v1", Content: raw,
	})
	check(err)
	records, err := (&tdx.Client{}).NormalizeProfessionalFinancialPackage(financial.FileEntry{Filename: "gpcw20250930.zip"}, raw, stored.ArtifactID)
	check(err)
	if len(records) != 1 || records[0].ProviderCode != "600113" || len(records[0].ProviderFields) != 584 {
		t.Fatal("unexpected provider slice")
	}
	want, err := strconv.ParseFloat(evidence.Corrected.Value, 32)
	check(err)
	old, err := strconv.ParseFloat(evidence.Original.Value, 32)
	check(err)
	bits := records[0].ProviderFields[229].Bits
	if bits != math.Float32bits(float32(want)) || bits == math.Float32bits(float32(old)) {
		t.Fatalf("current FN230 bits=%08x, not the corrected PDF value", bits)
	}
	resolved, _, err := resolveProviderFinancialRecords(ctx, db, records)
	check(err)
	if len(resolved) != 1 {
		t.Fatal("unresolved correction sample")
	}
	_, err = duckstore.ReconcileProviderFinancialRecordsForArtifact(ctx, db, runID, "tdx", stored.SHA256, resolved)
	check(err)
	check(duckstore.FinishIngestRun(ctx, db, runID, duckstore.IngestRunCompleted, nil, nil))
	result, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if result.Linked != 1 || result.Inserted != 41 || result.Rejected != 4 || result.LinkAmbiguous != 0 {
		t.Fatalf("corrected materialization: %+v", result)
	}
	comparisons, err := csv.NewReader(bytes.NewReader(readFinancialSample(t, dir, "comparison.csv"))).ReadAll()
	check(err)
	if len(comparisons) != 5 || len(comparisons[0]) != 5 {
		t.Fatal("expected four comparison rows")
	}
	for _, row := range comparisons[1:] {
		if row[1] == "" { // 利润总额不冒充已审核的营业利润字段。
			continue
		}
		field, err := strconv.Atoi(row[1][2:])
		check(err)
		corrected, err := strconv.ParseFloat(row[3], 32)
		check(err)
		original, err := strconv.ParseFloat(row[2], 32)
		check(err)
		bits := records[0].ProviderFields[field-1].Bits
		if bits != math.Float32bits(float32(corrected)) || bits == math.Float32bits(float32(original)) {
			t.Fatalf("%s does not match corrected PDF", row[1])
		}
		var value float64
		check(db.QueryRowContext(ctx, `SELECT CAST(value AS DOUBLE) FROM fundamental.fact
			WHERE source_provider_field=? AND period_type='Q3' AND unit='CNY'`, row[1]).Scan(&value))
		if value != corrected {
			t.Fatalf("%s canonical value=%v, want %v", row[1], value, corrected)
		}
	}
	var filingID, variant, period string
	check(db.QueryRowContext(ctx, `SELECT a.source_filing_id, a.filing_variant, f.period_type
		FROM fundamental.fact f JOIN fundamental.filing a ON a.filing_id=f.source_filing_id
		WHERE f.canonical_field='revenue'`).Scan(&filingID, &variant, &period))
	if filingID != evidence.Corrected.ID || variant != "corrected_report" || period != "Q3" {
		t.Fatalf("linked filing=%s variant=%s period=%s", filingID, variant, period)
	}
	reportPeriod := time.Date(2025, 9, 30, 0, 0, 0, 0, time.UTC)
	originalAvailable := time.Date(2025, 10, 31, 16, 0, 0, 0, time.UTC)
	correctedAvailable := time.Date(2025, 11, 1, 16, 0, 0, 0, time.UTC)
	assertPITRevenue(t, ctx, db, id, reportPeriod, originalAvailable, false, 0)
	assertPITRevenue(t, ctx, db, id, reportPeriod, correctedAvailable.Add(-time.Second), false, 0)
	assertPITRevenue(t, ctx, db, id, reportPeriod, correctedAvailable, true, want)
	replay, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if replay.Inserted != 0 || replay.Updated != 0 || replay.Removed != 0 || replay.Materialized != 41 {
		t.Fatalf("correction replay: %+v", replay)
	}
	t.Log("已验证更正后值及原始版本缺失时的保守边界；未验证真实 TDX 旧值→新值转换")
}
