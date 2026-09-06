package ingest

import (
	"crypto/sha256"
	"database/sql"
	"encoding/json"
	"fmt"
	"math"
	"math/big"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/artifact"
	"github.com/yinhm/alphalake/internal/domain"
	"github.com/yinhm/alphalake/internal/source/cninfo"
	"github.com/yinhm/alphalake/internal/source/tdx"
	"github.com/yinhm/alphalake/internal/source/tdx/financial"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestRealSixQuarterWindows(t *testing.T) {
	const dir = "testdata/ttm-2026"
	ctx := t.Context()
	check := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "ttm.duckdb"))
	check(err)
	defer db.Close()
	root := filepath.Join(t.TempDir(), "raw")
	var instruments []domain.InstrumentObservation
	check(json.Unmarshal(readAnnualSample(t, "instruments.json"), &instruments))
	_, err = duckstore.UpsertInstruments(ctx, db, instruments)
	check(err)
	run, err := duckstore.StartIngestRun(ctx, db, "tdx", "acceptance_sample", nil)
	check(err)
	keep := func(code string) bool { return code == "002213" || code == "002920" }
	for _, path := range []string{quarterSampleDir + "/002213-catalogue.json", quarterSampleDir + "/002920-catalogue.json", dir + "/002213-catalogue.json", dir + "/002920-catalogue.json", "testdata/annual-2025/page-1.json", "testdata/annual-2025/page-2.json", "testdata/annual-2025/page-3.json"} {
		raw := readFinancialSample(t, filepath.Dir(path), filepath.Base(path))
		stored, err := artifact.Persist(ctx, db, root, artifact.Input{Source: "cninfo", Dataset: "filing_catalogue", SourceLocator: path, FetchedAt: time.Now(), MediaType: "application/json", ParserVersion: cninfo.CatalogueParserVersion, Content: raw})
		check(err)
		page, err := cninfo.ParseCataloguePage(raw)
		check(err)
		for _, f := range page.Filings {
			if keep(f.ProviderCode) {
				f.CatalogueArtifactID = stored.ArtifactID
				_, err = duckstore.UpsertFilings(ctx, db, run, []domain.FilingObservation{f})
				check(err)
			}
		}
	}
	type captured struct {
		File      string
		SampleSHA string    `json:"sample_sha256"`
		FetchedAt time.Time `json:"fetched_at"`
	}
	var old, newer []captured
	check(json.Unmarshal(readFinancialSample(t, quarterSampleDir, "packages.json"), &old))
	check(json.Unmarshal(readFinancialSample(t, dir, "packages.json"), &newer))
	annualTime, err := time.Parse(time.RFC3339Nano, "2026-09-05T06:58:06.982587Z")
	check(err)
	packages := append(append(old, captured{File: "gpcw20251231.zip", FetchedAt: annualTime, SampleSHA: "65dce4ff53903ee49e5abe17e106faeebcd5a8e1419a7d2a0f9f3c3abfafd0ce"}), newer...)
	if len(packages) != 6 {
		t.Fatal("expected six source periods")
	}
	records := map[string]financial.Record{}
	for i, p := range packages {
		sampleDir := quarterSampleDir
		if i == 3 {
			sampleDir = "testdata/annual-2025"
		}
		if i > 3 {
			sampleDir = dir
		}
		raw := readFinancialSample(t, sampleDir, p.File)
		if fmt.Sprintf("%x", sha256.Sum256(raw)) != p.SampleSHA {
			t.Fatal("source slice hash mismatch", p.File)
		}
		pkg, err := financial.ParsePackage(p.File, raw)
		check(err)
		for _, r := range pkg.Records {
			if keep(r.Code) {
				records[r.Code+"/"+r.ReportPeriod.Format("2006-01-02")] = r
			}
		}
		stored, err := artifact.Persist(ctx, db, root, artifact.Input{Source: "tdx", Dataset: "acceptance_sample", SourceLocator: "sample/" + p.File, FetchedAt: p.FetchedAt, MediaType: "application/zip", ParserVersion: "gpcw-v1", Content: raw})
		check(err)
		normalized, err := (&tdx.Client{}).NormalizeProfessionalFinancialPackage(financial.FileEntry{Filename: p.File}, raw, stored.ArtifactID)
		check(err)
		var selected []domain.ProviderFinancialRecord
		for _, r := range normalized {
			if keep(r.ProviderCode) {
				selected = append(selected, r)
			}
		}
		resolved, _, err := resolveProviderFinancialRecords(ctx, db, selected)
		check(err)
		if len(resolved) != 2 {
			t.Fatal("unresolved sample identities")
		}
		_, err = duckstore.ReconcileProviderFinancialRecordsForArtifact(ctx, db, run, "tdx", stored.SHA256, resolved)
		check(err)
	}
	check(duckstore.FinishIngestRun(ctx, db, run, duckstore.IngestRunCompleted, nil, nil))
	materialized, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if len(records) != 12 || materialized.Linked != 12 || materialized.Materialized != 569 || materialized.Rejected != 43 {
		t.Fatalf("six quarters: records=%d result=%+v", len(records), materialized)
	}
	rows := financialSampleValues(t, dir, "reported.csv", 38)
	printed := map[string]*big.Rat{}
	decimal := func(s string) *big.Rat {
		t.Helper()
		v, ok := new(big.Rat).SetString(s)
		if !ok {
			t.Fatal(s)
		}
		return v
	}
	key := func(code, field, period string) string { return code + "/" + field + "/" + period }
	for _, r := range append(financialSampleValues(t, quarterSampleDir, "cumulative.csv", 24), rows...) {
		printed[key(r[0], r[1], r[2])] = decimal(r[5])
	}
	for _, r := range rows {
		id := strings.TrimSuffix(filepath.Base(r[7]), ".PDF")
		pdfDir := dir
		if id == "1224532935" {
			pdfDir = "testdata/core-financial-2025"
		}
		if fmt.Sprintf("%x", sha256.Sum256(readFinancialSample(t, pdfDir, id+".pdf"))) != r[8] {
			t.Fatal("PDF hash mismatch", id)
		}
		field, err := strconv.Atoi(r[1][2:])
		check(err)
		// Annual headline amounts are independently compared to four-quarter sums below.
		if r[2] == "2025-12-31" && field >= 230 && field <= 237 {
			continue
		}
		v := new(big.Rat).Set(printed[key(r[0], r[1], r[2])])
		if r[2] == "2026-06-30" && field >= 230 && field <= 237 {
			v.Sub(v, printed[key(r[0], r[1], "2026-03-31")])
		}
		expected, err := strconv.ParseFloat(v.FloatString(2), 32)
		check(err)
		if got := records[r[0]+"/"+r[2]].Fields[field-1].Bits; got != math.Float32bits(float32(expected)) {
			t.Fatalf("%s source bits=%08x expected=%v", r[:3], got, expected)
		}
	}
	ends := []string{"2025-12-31", "2026-03-31", "2026-06-30"}
	periods := []string{"2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"}
	for _, code := range []string{"002213", "002920"} {
		for j, end := range ends {
			for _, field := range []int{230, 232, 233, 234} {
				var want, bound float64
				for _, p := range periods[j : j+4] {
					v := float32(records[code+"/"+p].Fields[field-1].Value)
					want += float64(v)
					bound += math.Max(math.Abs(float64(math.Nextafter32(v, float32(math.Inf(1))))-float64(v)), math.Abs(float64(math.Nextafter32(v, float32(math.Inf(-1))))-float64(v))) / 2
				}
				fn := fmt.Sprintf("FN%d", field)
				total := new(big.Rat).Set(printed[key(code, fn, "2025-12-31")])
				if j > 0 {
					total.Add(total, printed[key(code, fn, end)])
					total.Sub(total, printed[key(code, fn, "2025"+end[4:])])
				}
				pdfValue, _ := total.Float64()
				if math.Abs(want-pdfValue) > bound+0.01 {
					t.Fatalf("%s %s %s: four quarters=%v PDF annual+YTD=%v bound=%v", code, fn, end, want, pdfValue, bound)
				}
				var value float64
				var inputs int
				check(db.QueryRowContext(ctx, `SELECT CAST(value AS DOUBLE), available_inputs FROM fundamental.ttm_asof('2026-09-06', CAST(? AS DATE)) WHERE provider_code=? AND source_provider_field=? AND coverage_status='complete'`, end, code, fn).Scan(&value, &inputs))
				if value != want || inputs != 4 {
					t.Fatalf("TTM %s %s %s=%v want=%v inputs=%d", code, fn, end, value, want, inputs)
				}
			}
		}
		for _, field := range []int{93, 114, 8} {
			want := records[code+"/2026-06-30"].Fields[field-1].Value
			required := 1
			if field != 8 {
				want += records[code+"/2025-12-31"].Fields[field-1].Value - records[code+"/2025-06-30"].Fields[field-1].Value
				required = 3
			}
			var value float64
			var n int
			check(db.QueryRowContext(ctx, `SELECT CAST(value AS DOUBLE), available_inputs FROM fundamental.ttm_asof('2026-09-06', DATE '2026-06-30') WHERE provider_code=? AND source_provider_field=? AND coverage_status='complete'`, code, fmt.Sprintf("FN%d", field)).Scan(&value, &n))
			if value != want || n != required {
				t.Fatalf("YTD/stock %s FN%d value=%v want=%v n=%d", code, field, value, want, n)
			}
		}
		disclosed := "2026-08-21"
		if code == "002920" {
			disclosed = "2026-08-14"
		}
		date, err := time.Parse("2006-01-02", disclosed)
		check(err)
		available := date.Add(16 * time.Hour)
		var before sql.NullFloat64
		var n int
		check(db.QueryRowContext(ctx, `SELECT CAST(value AS DOUBLE), available_inputs FROM fundamental.ttm_asof(?, DATE '2026-06-30') WHERE provider_code=? AND canonical_field='revenue'`, available.Add(-time.Second), code).Scan(&before, &n))
		if before.Valid || n != 3 {
			t.Fatal("incomplete TTM visible before H1 disclosure")
		}
		check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.ttm_asof(?, DATE '2026-06-30') WHERE provider_code=? AND coverage_status='complete'`, available, code).Scan(&n))
		if n != 51 {
			t.Fatalf("%s complete H1 inputs=%d want=51", code, n)
		}
	}
	replay, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if replay.Inserted != 0 || replay.Updated != 0 || replay.Removed != 0 {
		t.Fatalf("replay=%+v", replay)
	}
	for _, end := range periods {
		var complete, total int
		check(db.QueryRowContext(ctx, `SELECT count(*) FILTER(WHERE coverage_status='complete'), count(*) FROM fundamental.ttm_asof('2026-09-06', CAST(? AS DATE))`, end).Scan(&complete, &total))
		t.Logf("%s：完整窗口 %d/%d", end, complete, total)
	}
}
