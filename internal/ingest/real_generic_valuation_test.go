package ingest

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"math"
	"os"
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

func TestRealGenericValuationSourceChain(t *testing.T) {
	verifyGenericValuationSample(t, "testdata/generic-valuation-2026", `[
	 {"Instrument":{"Type":"equity","ExchangeMIC":"XSHE","Currency":"CNY","Name":"汇川技术"},"Identifier":{"Provider":"tdx","Type":"symbol","Value":"sz300124"}},
	 {"Instrument":{"Type":"equity","ExchangeMIC":"XSHG","Currency":"CNY","Name":"海天味业"},"Identifier":{"Provider":"tdx","Type":"symbol","Value":"sh603288"}}
	]`, 8, 65, 7, "ALPHALAKE_GENERIC_EXPORT_DIR")
}

func TestRealBearValuationSourceChain(t *testing.T) {
	verifyGenericValuationSample(t, "testdata/bear-valuation-2026", `[{"Instrument":{"Type":"equity","ExchangeMIC":"XSHE","Currency":"CNY","Name":"小熊电器"},"Identifier":{"Provider":"tdx","Type":"symbol","Value":"sz002959"}}]`, 5, 35, 1, "ALPHALAKE_BEAR_EXPORT_DIR")
}

func verifyGenericValuationSample(t *testing.T, dir, instrumentsJSON string, reportCount, comparisonCount, expectedDifferences int, exportEnv string) {
	ctx := t.Context()
	check := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	read := func(name string, value any) {
		t.Helper()
		check(json.Unmarshal(readFinancialSample(t, dir, name), value))
	}
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "generic.duckdb"))
	check(err)
	defer db.Close()
	var instruments []domain.InstrumentObservation
	check(json.Unmarshal([]byte(instrumentsJSON), &instruments))
	codes := []string{}
	for _, instrument := range instruments {
		codes = append(codes, instrument.Identifier.Value[2:])
	}
	_, err = duckstore.UpsertInstruments(ctx, db, instruments)
	check(err)
	run, err := duckstore.StartIngestRun(ctx, db, "tdx", "generic_acceptance_sample", nil)
	check(err)
	// 样本固定观察时钟；不是生产采集时间或历史主数据验收。
	at := time.Date(2026, 9, 10, 0, 0, 0, 0, time.UTC)
	rawRoot := filepath.Join(t.TempDir(), "raw")
	persist := func(source, dataset, name, mime string, raw []byte) int64 {
		t.Helper()
		stored, err := artifact.Persist(ctx, db, rawRoot, artifact.Input{Source: source, Dataset: dataset, SourceLocator: name, FetchedAt: at, MediaType: mime, ParserVersion: "acceptance-v1", Content: raw})
		check(err)
		return stored.ArtifactID
	}
	var reports map[string]struct {
		File, SHA256, URL string
		AnnouncementID    string `json:"announcement_id"`
	}
	read("reports.json", &reports)
	if len(reports) != reportCount {
		t.Fatal("PDF count", len(reports), reportCount)
	}
	documents := map[string]int64{}
	hashes := map[string]string{}
	for _, report := range reports {
		raw := readFinancialSample(t, dir, report.File)
		if fmt.Sprintf("%x", sha256.Sum256(raw)) != report.SHA256 {
			t.Fatal("PDF hash", report.File)
		}
		documents[report.AnnouncementID] = persist("cninfo", "filing_document", report.URL, "application/pdf", raw)
		hashes[report.AnnouncementID] = report.SHA256
	}
	docClient, err := cninfo.NewDefaultClient()
	check(err)
	for _, code := range codes {
		raw := readFinancialSample(t, dir, code+"-catalogue.json")
		id := persist("cninfo", "filing_catalogue", code+"-catalogue.json", "application/json", raw)
		page, err := cninfo.ParseCataloguePage(raw)
		check(err)
		for _, filing := range page.Filings {
			if filing.ProviderCode != code {
				continue
			}
			filing.CatalogueArtifactID = id
			filing.SourceURL, err = docClient.FilingDocumentURL(filing.DocumentLocator)
			check(err)
			filing.DocumentArtifactID = documents[filing.SourceFilingID]
			filing.DocumentSHA256 = hashes[filing.SourceFilingID]
			_, err = duckstore.UpsertFilings(ctx, db, run, []domain.FilingObservation{filing})
			check(err)
		}
	}
	var packages []struct{ File, SHA256 string }
	read("packages.json", &packages)
	if len(packages) != 6 {
		t.Fatal("six periods required")
	}
	records := map[string]financial.Record{}
	for _, p := range packages {
		raw := readFinancialSample(t, dir, p.File)
		if fmt.Sprintf("%x", sha256.Sum256(raw)) != p.SHA256 {
			t.Fatal("package hash", p.File)
		}
		pkg, err := financial.ParsePackage(p.File, raw)
		check(err)
		if len(pkg.Records) != len(instruments) {
			t.Fatal("company count", p.File)
		}
		for _, record := range pkg.Records {
			records[record.Code+record.ReportPeriod.Format("20060102")] = record
		}
		id := persist("tdx", "professional_financial", p.File, "application/zip", raw)
		normalized, err := (&tdx.Client{}).NormalizeProfessionalFinancialPackage(financial.FileEntry{Filename: p.File}, raw, id)
		check(err)
		resolved, _, err := resolveProviderFinancialRecords(ctx, db, normalized)
		check(err)
		if len(resolved) != len(instruments) {
			t.Fatal("unresolved sample")
		}
		_, err = duckstore.ReconcileProviderFinancialRecordsForArtifact(ctx, db, run, "tdx", p.SHA256, resolved)
		check(err)
	}
	var ledger []struct {
		Code, Period, Comparison string
		Field                    int
		SourceBits               uint32 `json:"source_bits"`
		Encoded                  string `json:"encoded_pdf_value"`
	}
	read("values.json", &ledger)
	if len(ledger) != comparisonCount {
		t.Fatal("comparison count", len(ledger), comparisonCount)
	}
	differences := 0
	for _, row := range ledger {
		actual := records[row.Code+row.Period].Fields[row.Field-1].Bits
		if actual != row.SourceBits {
			t.Fatal("source changed", row)
		}
		expected, err := strconv.ParseFloat(row.Encoded, 32)
		check(err)
		equal := math.Float32bits(float32(expected)) == actual
		if equal != (row.Comparison == "equal_float32") {
			t.Fatal("PDF comparison changed", row)
		}
		if !equal {
			differences++
		}
	}
	if differences != expectedDifferences {
		t.Fatal("retain reviewed differences", differences, expectedDifferences)
	}
	check(duckstore.FinishIngestRun(ctx, db, run, duckstore.IngestRunCompleted, nil, nil))
	result, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if result.Linked != 6*len(instruments) {
		t.Fatalf("all source periods must link: %+v", result)
	}
	replay, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if replay.Inserted != 0 || replay.Updated != 0 || replay.Removed != 0 {
		t.Fatalf("unchanged source must replay without fact changes: %+v", replay)
	}
	for _, code := range codes {
		snapshot, err := duckstore.ExportValuationData(ctx, db, code, time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC), at)
		check(err)
		payload, err := json.MarshalIndent(snapshot, "", "  ")
		check(err)
		var decoded struct {
			Facts []struct {
				Field, Period, Value, Unit string
				SourceProviderField        string `json:"source_provider_field"`
				Bits                       uint32
				Multiplier                 float64
			}
			Windows []json.RawMessage
		}
		check(json.Unmarshal(payload, &decoded))
		if len(decoded.Facts) == 0 || len(decoded.Windows) == 0 {
			t.Fatal("empty standard export", code)
		}
		for _, row := range ledger {
			if row.Code != code {
				continue
			}
			period, err := time.Parse("20060102", row.Period)
			check(err)
			found := 0
			for _, fact := range decoded.Facts {
				if fact.SourceProviderField != fmt.Sprintf("FN%d", row.Field) || fact.Period != period.Format("2006-01-02") {
					continue
				}
				found++
				value, err := strconv.ParseFloat(fact.Value, 64)
				check(err)
				multiplier := float64(1)
				if row.Field == 439 {
					multiplier = 10000
				}
				unit := "CNY"
				if row.Field == 238 {
					unit = "share"
				}
				if fact.Bits != row.SourceBits || fact.Multiplier != multiplier || fact.Unit != unit || value != float64(math.Float32frombits(row.SourceBits))*multiplier {
					t.Fatal("standard fact must preserve source, not replace with PDF", row, fact)
				}
			}
			if found != 1 {
				t.Fatal("one standard fact required", row, found)
			}
		}
		if output := os.Getenv(exportEnv); output != "" {
			check(os.MkdirAll(output, 0755))
			check(os.WriteFile(filepath.Join(output, code+".json"), payload, 0644))
		}
	}
}
