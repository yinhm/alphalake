package ingest

import (
	"bytes"
	"crypto/sha256"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/artifact"
	"github.com/yinhm/alphalake/internal/domain"
	"github.com/yinhm/alphalake/internal/source/cninfo"
	"github.com/yinhm/alphalake/internal/source/tdx"
	"github.com/yinhm/alphalake/internal/source/tdx/financial"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

// 使用生产归一化、身份解析、物化和查询；CSV 是查询快照，不是直接解码包生成的替代事实。
func TestRealValuationStandardChain(t *testing.T) {
	const dir = "testdata/valuation-chain-2026"
	ctx := t.Context()
	check := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	dbPath := filepath.Join(t.TempDir(), "valuation.duckdb")
	db, err := duckstore.OpenAndMigrate(ctx, dbPath)
	check(err)
	defer db.Close()
	root := filepath.Join(t.TempDir(), "raw")
	var instruments []domain.InstrumentObservation
	check(json.Unmarshal(readFinancialSample(t, dir, "instruments.json"), &instruments))
	_, err = duckstore.UpsertInstruments(ctx, db, instruments)
	check(err)
	run, err := duckstore.StartIngestRun(ctx, db, "tdx", "valuation_acceptance_sample", nil)
	check(err)
	for _, path := range []string{dir + "/600519-catalogue.json", dir + "/300866-catalogue.json", "testdata/moutai-valuation-2026/catalogue.json", "testdata/anker-valuation-2026/catalogue.json"} {
		raw := readFinancialSample(t, filepath.Dir(path), filepath.Base(path))
		var meta struct {
			FetchedAt time.Time `json:"fetched_at"`
			SHA       string    `json:"sha256"`
		}
		if filepath.Dir(path) == dir {
			check(json.Unmarshal(readFinancialSample(t, dir, filepath.Base(path[:len(path)-5])+".meta.json"), &meta))
		} else {
			var reports map[string]json.RawMessage
			check(json.Unmarshal(readFinancialSample(t, filepath.Dir(path), "reports.json"), &reports))
			check(json.Unmarshal(reports["catalogue"], &meta))
		}
		if fmt.Sprintf("%x", sha256.Sum256(raw)) != meta.SHA {
			t.Fatal("catalogue hash", path)
		}
		stored, err := artifact.Persist(ctx, db, root, artifact.Input{Source: "cninfo", Dataset: "filing_catalogue", SourceLocator: path, FetchedAt: meta.FetchedAt, MediaType: "application/json", ParserVersion: cninfo.CatalogueParserVersion, Content: raw})
		check(err)
		page, err := cninfo.ParseCataloguePage(raw)
		check(err)
		for _, f := range page.Filings {
			if f.ProviderCode == "600519" || f.ProviderCode == "300866" {
				f.CatalogueArtifactID = stored.ArtifactID
				_, err = duckstore.UpsertFilings(ctx, db, run, []domain.FilingObservation{f})
				check(err)
			}
		}
	}
	var packages []struct {
		File      string
		SHA       string    `json:"sample_sha256"`
		FetchedAt time.Time `json:"fetched_at"`
	}
	check(json.Unmarshal(readFinancialSample(t, dir, "packages.json"), &packages))
	if len(packages) != 6 {
		t.Fatal("six periods required")
	}
	for _, p := range packages {
		raw := readFinancialSample(t, dir, p.File)
		if fmt.Sprintf("%x", sha256.Sum256(raw)) != p.SHA {
			t.Fatal("package hash", p.File)
		}
		stored, err := artifact.Persist(ctx, db, root, artifact.Input{Source: "tdx", Dataset: "valuation_acceptance_sample", SourceLocator: "sample/" + p.File, FetchedAt: p.FetchedAt, MediaType: "application/zip", ParserVersion: "gpcw-v1", Content: raw})
		check(err)
		records, err := (&tdx.Client{}).NormalizeProfessionalFinancialPackage(financial.FileEntry{Filename: p.File}, raw, stored.ArtifactID)
		check(err)
		resolved, _, err := resolveProviderFinancialRecords(ctx, db, records)
		check(err)
		if len(resolved) != 2 {
			t.Fatal("sample identities")
		}
		_, err = duckstore.ReconcileProviderFinancialRecordsForArtifact(ctx, db, run, "tdx", stored.SHA256, resolved)
		check(err)
	}
	check(duckstore.FinishIngestRun(ctx, db, run, duckstore.IngestRunCompleted, nil, nil))
	result, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if result.Linked != 12 {
		rows, err := db.QueryContext(ctx, `SELECT provider_code, report_period, status, candidate_count FROM fundamental.provider_filing_link WHERE status<>'linked'`)
		check(err)
		for rows.Next() {
			var a, b, c, d string
			check(rows.Scan(&a, &b, &c, &d))
			t.Log(a, b, c, d)
		}
		rows.Close()
		t.Fatalf("links: %+v", result)
	}
	check(db.Close())
	db, err = duckstore.OpenAndMigrate(ctx, dbPath)
	check(err)
	defer db.Close()
	export := func(name, query string) {
		t.Helper()
		rows, err := db.QueryContext(ctx, query)
		check(err)
		defer rows.Close()
		columns, err := rows.Columns()
		check(err)
		var out bytes.Buffer
		w := csv.NewWriter(&out)
		check(w.Write(columns))
		for rows.Next() {
			values := make([]string, len(columns))
			dest := make([]any, len(columns))
			for i := range values {
				dest[i] = &values[i]
			}
			check(rows.Scan(dest...))
			check(w.Write(values))
		}
		check(rows.Err())
		w.Flush()
		check(w.Error())
		path := filepath.Join(dir, name+".csv")
		if os.Getenv("ALPHALAKE_WRITE_VALUATION_CHAIN") == "1" {
			check(os.WriteFile(path, out.Bytes(), 0644))
		} else if !bytes.Equal(readFinancialSample(t, dir, name+".csv"), out.Bytes()) {
			t.Fatalf("%s differs from production query", name)
		}
	}
	export("facts", `SELECT f.provider_code AS code, cast(f.report_period AS VARCHAR) AS period, f.source_provider_field AS field, f.canonical_field, f.period_type, f.statement_scope, f.unit, cast(f.value AS VARCHAR) AS value, cast(p.value_float32_bits AS VARCHAR) AS bits, cast(m.value_multiplier AS VARCHAR) AS multiplier, f.revision_key AS artifact_sha256, fi.source_filing_id AS announcement_id, cast(f.announcement_time AS VARCHAR) AS available_at
 FROM fundamental.fact_asof('2026-09-06') f JOIN fundamental.provider_fact p ON p.provider_fact_id=f.provider_fact_id JOIN fundamental.provider_field m ON m.source=f.primary_source AND m.provider_field=f.source_provider_field JOIN fundamental.filing fi ON fi.filing_id=f.source_filing_id ORDER BY code,period,field`)
	export("windows", `SELECT provider_code AS code, cast(report_period AS VARCHAR) AS period, source_provider_field AS field, canonical_field, calculation_basis, coverage_status, coalesce(cast(value AS VARCHAR),'') AS value, cast(required_inputs AS VARCHAR) AS required_inputs,cast(available_inputs AS VARCHAR) AS available_inputs, cast(input_periods AS VARCHAR) AS input_periods, cast(input_coefficients AS VARCHAR) AS coefficients FROM fundamental.ttm_asof('2026-09-06', DATE '2026-06-30') ORDER BY code,field`)
	replay, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if replay.Inserted != 0 || replay.Updated != 0 || replay.Removed != 0 {
		t.Fatalf("replay %+v", replay)
	}
	for _, code := range []string{"600519", "300866"} {
		var available time.Time
		check(db.QueryRowContext(ctx, `SELECT min(announcement_time) FROM fundamental.fact WHERE provider_code=? AND report_period=DATE '2026-06-30'`, code).Scan(&available))
		var n int
		check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.fact_asof(?) WHERE provider_code=? AND report_period=DATE '2026-06-30'`, available.Add(-time.Second), code).Scan(&n))
		if n != 0 {
			t.Fatal("future H1 visible")
		}
		check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.fact_asof(?) WHERE provider_code=? AND report_period=DATE '2026-06-30'`, available, code).Scan(&n))
		if n == 0 {
			t.Fatal("H1 missing at disclosure")
		}
	}
	// 修改映射依据后必须撤销标准值，不能回退 PDF；恢复后从源证据重建。
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET value_multiplier=-1 WHERE provider_field='FN8' AND source='tdx'`)
	check(err)
	invalid, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if invalid.Removed != 12 {
		t.Fatalf("invalid mapping %+v", invalid)
	}
	var n int
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.ttm_asof('2026-09-06',DATE '2026-06-30') WHERE source_provider_field='FN8' AND value IS NOT NULL`).Scan(&n))
	if n != 0 {
		t.Fatal("invalid cash still queryable")
	}
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET value_multiplier=1 WHERE provider_field='FN8' AND source='tdx'`)
	check(err)
	restored, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if restored.Inserted != 12 {
		t.Fatalf("restore %+v", restored)
	}
	t.Logf("production chain: %+v", result)
}
