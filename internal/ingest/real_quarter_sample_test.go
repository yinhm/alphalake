package ingest

import (
	"encoding/json"
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

const quarterSampleDir = "testdata/quarters-2025"

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

func TestRealQuarterFinancialWorkflow(t *testing.T) {
	ctx := t.Context()
	check := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "quarters.duckdb"))
	check(err)
	defer db.Close()
	root := filepath.Join(t.TempDir(), "raw")
	var instruments []domain.InstrumentObservation
	check(json.Unmarshal(readAnnualSample(t, "instruments.json"), &instruments))
	_, err = duckstore.UpsertInstruments(ctx, db, instruments)
	check(err)
	runID, err := duckstore.StartIngestRun(ctx, db, "tdx", "acceptance_sample", nil)
	check(err)
	observed := time.Date(2026, 9, 5, 16, 52, 0, 0, time.UTC)
	for _, code := range []string{"002213", "002920"} {
		raw := readFinancialSample(t, quarterSampleDir, code+"-catalogue.json")
		stored, err := artifact.Persist(ctx, db, root, artifact.Input{
			Source: "cninfo", Dataset: "filing_catalogue", SourceLocator: "sample/" + code + "-catalogue.json",
			FetchedAt: observed, MediaType: "application/json", ParserVersion: cninfo.CatalogueParserVersion, IngestRunID: &runID, Content: raw,
		})
		check(err)
		page, err := cninfo.ParseCataloguePage(raw)
		check(err)
		if len(page.Filings) != 4 || len(page.Issues) != 0 {
			t.Fatalf("unexpected catalogue: %+v", page)
		}
		for i := range page.Filings {
			page.Filings[i].CatalogueArtifactID = stored.ArtifactID
		}
		// Retain unresolved observations; the production materializer resolves them locally.
		_, err = duckstore.UpsertFilings(ctx, db, runID, page.Filings)
		check(err)
	}
	var packages []struct {
		File      string    `json:"file"`
		FetchedAt time.Time `json:"fetched_at"`
	}
	check(json.Unmarshal(readFinancialSample(t, quarterSampleDir, "packages.json"), &packages))
	if len(packages) != 3 {
		t.Fatal("expected three captured financial packages")
	}
	for _, pkg := range packages {
		name := pkg.File
		raw := readFinancialSample(t, quarterSampleDir, name)
		stored, err := artifact.Persist(ctx, db, root, artifact.Input{
			Source: "tdx", Dataset: "acceptance_sample", SourceLocator: "sample/" + name,
			FetchedAt: pkg.FetchedAt, MediaType: "application/zip", ParserVersion: "gpcw-v1", IngestRunID: &runID, Content: raw,
		})
		check(err)
		records, err := (&tdx.Client{}).NormalizeProfessionalFinancialPackage(financial.FileEntry{Filename: name}, raw, stored.ArtifactID)
		check(err)
		resolved, resolutions, err := resolveProviderFinancialRecords(ctx, db, records)
		check(err)
		state, err := duckstore.ApplyProviderFinancialResolutions(ctx, db, runID, resolutions)
		check(err)
		if len(resolved) != 2 || state.Pending != 0 {
			t.Fatalf("unresolved quarter identities: %+v", state)
		}
		written, err := duckstore.ReconcileProviderFinancialRecordsForArtifact(ctx, db, runID, "tdx", stored.SHA256, resolved)
		check(err)
		if written.Inserted != 2*584 {
			t.Fatalf("quarter provider facts: %+v", written)
		}
	}
	check(duckstore.FinishIngestRun(ctx, db, runID, duckstore.IngestRunCompleted, nil, nil))
	result, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if result.FilingResolutionRecovered != 8 || result.FilingResolutionPending != 0 || result.Linked != 6 || result.Inserted != 54 || result.Rejected != 0 || result.LinkPending != 0 || result.LinkAmbiguous != 0 {
		t.Fatalf("quarter materialization: %+v", result)
	}
	values := financialSampleValues(t, quarterSampleDir, "values.csv", 24)
	cumulative := financialSampleValues(t, quarterSampleDir, "cumulative.csv", 24)
	for i, row := range values {
		// Original quarter/half-year disclosures define PIT, not the later annual cross-check.
		originalURL := cumulative[i][7]
		disclosed, err := time.Parse("2006-01-02", strings.Split(originalURL, "/")[4])
		check(err)
		available := disclosed.Add(16 * time.Hour) // China calendar next-day midnight.
		var value float64
		var period, unit, filingID string
		var announcement time.Time
		check(db.QueryRowContext(ctx, `SELECT cast(f.value AS DOUBLE), f.period_type, f.unit, a.source_filing_id, f.announcement_time
			FROM fundamental.fact f JOIN fundamental.filing a ON a.filing_id=f.source_filing_id
			WHERE f.provider_code=? AND f.source_provider_field=? AND f.report_period=cast(? AS DATE)`, row[0], row[1], row[2]).Scan(&value, &period, &unit, &filingID, &announcement))
		want, err := strconv.ParseFloat(row[5], 32)
		check(err)
		if value != want || period != row[3] || unit != row[4] || !announcement.Equal(available) || !strings.HasSuffix(originalURL, "/"+filingID+".PDF") {
			t.Fatalf("%s/%s/%s: value=%v period=%s unit=%s filing=%s announcement=%s", row[0], row[1], row[2], value, period, unit, filingID, announcement)
		}
		var before int
		check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.fact_asof(?)
			WHERE provider_code=? AND source_provider_field=? AND report_period=cast(? AS DATE)`, available.Add(-time.Second), row[0], row[1], row[2]).Scan(&before))
		if before != 0 {
			t.Fatalf("quarter fact visible before disclosure: %v", row[:3])
		}
		check(db.QueryRowContext(ctx, `SELECT cast(value AS DOUBLE) FROM fundamental.fact_asof(?)
			WHERE provider_code=? AND source_provider_field=? AND report_period=cast(? AS DATE)`, available, row[0], row[1], row[2]).Scan(&value))
		if value != want {
			t.Fatalf("quarter ASOF value=%v, want %v", value, want)
		}
	}
	replay, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if replay.Inserted != 0 || replay.Updated != 0 || replay.Removed != 0 || replay.Rejected != 0 || replay.Materialized != 54 {
		t.Fatalf("quarter materialization replay: %+v", replay)
	}
	t.Log("Q1/Q2/Q3：24 个金额、六个原始公告关联及各自 PIT 边界通过；54 条标准事实重放无变更")
}
