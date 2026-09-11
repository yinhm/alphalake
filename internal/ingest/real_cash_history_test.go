package ingest

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"math"
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

// 可选基库只读复制到全新目录；不在用户基库上迁移或导入。
func TestRealAnkerCashHistory(t *testing.T) {
	const dir = "testdata/anker-cash-history-2024"
	ctx := t.Context()
	check := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("ALPHALAKE_DUCKDB_MEMORY_LIMIT", "1GB")
	t.Setenv("ALPHALAKE_DUCKDB_THREADS", "2")
	base := os.Getenv("ALPHALAKE_CASH_HISTORY_BASE_DB")
	supplied := base != ""
	if !supplied {
		baseline := t.TempDir()
		t.Setenv("ALPHALAKE_VALUATION_EXPORT_DIR", baseline)
		if !t.Run("current_standard_fixture", TestRealValuationStandardChain) {
			t.Fatal("current fixture failed")
		}
		base = filepath.Join(baseline, "acceptance.duckdb")
	}
	if stat, err := os.Stat(base + ".wal"); err == nil && stat.Size() != 0 {
		t.Fatal("base WAL is not checkpointed")
	} else if err != nil && !os.IsNotExist(err) {
		check(err)
	}
	output := os.Getenv("ALPHALAKE_CASH_HISTORY_EXPORT_DIR")
	if output == "" {
		output = filepath.Join(t.TempDir(), "review")
	}
	check(os.MkdirAll(filepath.Dir(output), 0755))
	check(os.Mkdir(output, 0755))
	path := filepath.Join(output, "acceptance.duckdb")
	src, err := os.Open(base)
	check(err)
	dst, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	check(err)
	hash := sha256.New()
	_, err = io.Copy(io.MultiWriter(dst, hash), src)
	check(err)
	check(dst.Close())
	check(src.Close())
	baseHash := fmt.Sprintf("%x", hash.Sum(nil))
	db, err := duckstore.Open(ctx, path)
	check(err)
	defer func() { _ = db.Close() }()
	if !supplied {
		_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET valid_from=DATE '2025-01-01' WHERE source='tdx' AND provider_field='FN114'; DELETE FROM meta.schema_version WHERE version=38`)
		check(err)
	}
	var schema int
	check(db.QueryRowContext(ctx, `SELECT max(version) FROM meta.schema_version`).Scan(&schema))
	if schema != 37 {
		t.Fatalf("expected schema37 base, got %d", schema)
	}
	_, err = db.ExecContext(ctx, `CREATE TEMP TABLE cash_before AS SELECT * FROM fundamental.fact; CREATE TEMP TABLE cash_catalog_before AS SELECT * FROM fundamental.provider_field WHERE provider_field<>'FN114'`)
	check(err)
	asof, err := time.Parse(time.RFC3339Nano, "2026-09-10T22:18:56.906406Z")
	check(err)
	end := time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC)
	before, err := duckstore.ExportValuationData(ctx, db, "300866", end, asof)
	check(err)
	beforeJSON, err := json.Marshal(before)
	check(err)
	run, err := duckstore.StartIngestRun(ctx, db, "tdx", "reviewed_cash_history_2024", nil)
	check(err)
	root := filepath.Join(output, "raw")
	raw := readFinancialSample(t, dir, "catalogue.json")
	var request struct {
		AcquiredAt time.Time `json:"acquired_at"`
		SHA        string    `json:"sha256"`
	}
	check(json.Unmarshal(readFinancialSample(t, dir, "catalogue-request.json"), &request))
	if fmt.Sprintf("%x", sha256.Sum256(raw)) != request.SHA {
		t.Fatal("catalogue hash")
	}
	catalogue, err := artifact.Persist(ctx, db, root, artifact.Input{Source: "cninfo", Dataset: "filing_catalogue", SourceLocator: "reviewed/anker-cash-history-2024/catalogue", FetchedAt: request.AcquiredAt, MediaType: "application/json", ParserVersion: cninfo.CatalogueParserVersion, Content: raw})
	check(err)
	page, err := cninfo.ParseCataloguePage(raw)
	check(err)
	var reports []struct {
		ID        string `json:"announcement_id"`
		File      string
		URL       string
		SHA       string    `json:"sha256"`
		FetchedAt time.Time `json:"fetched_at"`
	}
	check(json.Unmarshal(readFinancialSample(t, dir, "reports.json"), &reports))
	for _, r := range reports {
		matches := 0
		for _, f := range page.Filings {
			if f.SourceFilingID != r.ID {
				continue
			}
			matches++
			if f.ProviderCode != "300866" {
				t.Fatal("filing identity")
			}
			pdf := readFinancialSample(t, dir, r.File)
			if fmt.Sprintf("%x", sha256.Sum256(pdf)) != r.SHA {
				t.Fatal("PDF hash")
			}
			a, err := artifact.Persist(ctx, db, root, artifact.Input{Source: "cninfo", Dataset: "filing_document", SourceLocator: r.URL, FetchedAt: r.FetchedAt, MediaType: "application/pdf", ParserVersion: "pdf-raw-v1", Content: pdf})
			check(err)
			f.CatalogueArtifactID = catalogue.ArtifactID
			f.SourceURL = r.URL
			f.DocumentArtifactID = a.ArtifactID
			f.DocumentSHA256 = a.SHA256
			_, err = duckstore.UpsertFilings(ctx, db, run, []domain.FilingObservation{f})
			check(err)
		}
		if matches != 1 {
			t.Fatalf("filing %s matches %d", r.ID, matches)
		}
	}
	var packages []struct {
		File      string
		SHA       string    `json:"sample_sha256"`
		FetchedAt time.Time `json:"fetched_at"`
	}
	check(json.Unmarshal(readFinancialSample(t, dir, "packages.json"), &packages))
	for _, p := range packages {
		raw := readFinancialSample(t, dir, p.File)
		if fmt.Sprintf("%x", sha256.Sum256(raw)) != p.SHA {
			t.Fatal("sample hash")
		}
		a, err := artifact.Persist(ctx, db, root, artifact.Input{Source: "tdx", Dataset: "reviewed_cash_history_2024", SourceLocator: "reviewed/" + p.File, FetchedAt: p.FetchedAt, MediaType: "application/zip", ParserVersion: "gpcw-v1", Content: raw})
		check(err)
		records, err := (&tdx.Client{}).NormalizeProfessionalFinancialPackage(financial.FileEntry{Filename: p.File}, raw, a.ArtifactID)
		check(err)
		resolved, _, err := resolveProviderFinancialRecords(ctx, db, records)
		check(err)
		if len(resolved) != 1 {
			t.Fatal("historical identity unresolved")
		}
		_, err = duckstore.ReconcileProviderFinancialRecordsForArtifact(ctx, db, run, "tdx", a.SHA256, resolved)
		check(err)
	}
	check(duckstore.FinishIngestRun(ctx, db, run, duckstore.IngestRunCompleted, nil, nil))
	initial, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	countCapex := func() int {
		var n int
		check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.fact WHERE provider_code='300866' AND source_provider_field='FN114' AND report_period BETWEEN DATE '2024-06-30' AND DATE '2024-12-31'`).Scan(&n))
		return n
	}
	if countCapex() != 0 {
		t.Fatal("unreviewed history was materialized")
	}
	check(duckstore.Apply(ctx, db))
	upgraded, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if upgraded.Inserted != 3 || upgraded.Updated != 0 || upgraded.Removed != 0 || countCapex() != 3 {
		t.Fatalf("scope upgrade %+v", upgraded)
	}
	var changed int
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM (SELECT * FROM cash_catalog_before EXCEPT SELECT * FROM fundamental.provider_field WHERE provider_field<>'FN114')`).Scan(&changed))
	if changed != 0 {
		t.Fatal("other mappings changed")
	}
	var evidence struct {
		Values []struct {
			Period, Field string
			Bits          uint32 `json:"source_bits"`
		} `json:"source_values"`
		Periods []struct {
			Period    string
			Available string `json:"available_from"`
		} `json:"periods"`
	}
	check(json.Unmarshal(readFinancialSample(t, dir, "verified.json"), &evidence))
	if len(evidence.Values) != 7 {
		t.Fatal("seven reviewed values required")
	}
	for _, r := range evidence.Values {
		var value float64
		var periodType, unit string
		check(db.QueryRowContext(ctx, `SELECT CAST(value AS DOUBLE),period_type,unit FROM fundamental.fact WHERE provider_code='300866' AND source_provider_field=? AND report_period=CAST(? AS DATE)`, r.Field, r.Period).Scan(&value, &periodType, &unit))
		want := map[string]string{"2024-06-30": "H1", "2024-09-30": "9M", "2024-12-31": "FY"}[r.Period]
		if r.Field != "FN114" {
			want = map[string]string{"2024-09-30": "Q3", "2024-12-31": "Q4"}[r.Period]
		}
		if value != float64(math.Float32frombits(r.Bits)) || periodType != want || unit != "CNY" {
			t.Fatalf("standard value differs %+v: %v %s %s", r, value, periodType, unit)
		}
	}
	for _, r := range evidence.Periods {
		at, err := time.Parse(time.RFC3339, r.Available)
		check(err)
		for _, offset := range []time.Duration{-time.Nanosecond, 0} {
			var n int
			check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.fact_asof(CAST(? AS TIMESTAMPTZ)) WHERE provider_code='300866' AND source_provider_field='FN114' AND report_period=CAST(? AS DATE)`, at.Add(offset), r.Period).Scan(&n))
			want := 0
			if offset == 0 {
				want = 1
			}
			if n != want {
				t.Fatal("disclosure boundary", r, n, want)
			}
		}
	}
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET valid_from=DATE '2025-01-01' WHERE source='tdx' AND provider_field='FN114'`)
	check(err)
	removed, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if removed.Removed != 3 || countCapex() != 0 {
		t.Fatal("withdrawal did not remove unsupported history", removed)
	}
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET valid_from=DATE '2024-06-30' WHERE source='tdx' AND provider_field='FN114'`)
	check(err)
	restored, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if restored.Inserted != 3 || countCapex() != 3 {
		t.Fatal("restoration failed", restored)
	}
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM (SELECT * FROM cash_before EXCEPT SELECT * FROM fundamental.fact)`).Scan(&changed))
	if changed != 0 {
		t.Fatalf("%d preexisting fact contents changed", changed)
	}
	var refreshed int
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM cash_before b JOIN fundamental.fact f USING(fact_id) WHERE b.ingest_run_id IS DISTINCT FROM f.ingest_run_id OR b.ingested_at IS DISTINCT FROM f.ingested_at`).Scan(&refreshed))
	after, err := duckstore.ExportValuationData(ctx, db, "300866", end, asof)
	check(err)
	afterJSON, err := json.Marshal(after)
	check(err)
	if !bytes.Equal(beforeJSON, afterJSON) {
		t.Fatal("current valuation inputs changed")
	}
	check(db.Close())
	db, err = duckstore.OpenAndMigrate(ctx, path)
	check(err)
	replay, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if replay.Inserted != 0 || replay.Updated != 0 || replay.Removed != 0 || countCapex() != 3 {
		t.Fatal("reopened replay changed facts", replay)
	}
	for name, period := range map[string]time.Time{"current.json": end, "prior.json": end.AddDate(-1, 0, 0)} {
		data, err := duckstore.ExportValuationData(ctx, db, "300866", period, asof)
		check(err)
		raw, err := json.MarshalIndent(data, "", "  ")
		check(err)
		check(os.WriteFile(filepath.Join(output, name), raw, 0644))
	}
	check(db.Close())
	src, err = os.Open(base)
	check(err)
	hash.Reset()
	_, err = io.Copy(hash, src)
	check(err)
	check(src.Close())
	if fmt.Sprintf("%x", hash.Sum(nil)) != baseHash {
		t.Fatal("base database changed")
	}
	receipt := map[string]any{"base_database": base, "base_sha256": baseHash, "database": path, "schema": 38, "initial": initial, "upgraded": upgraded, "removed": removed, "restored": restored, "replay": replay, "preexisting_fact_content_changed": changed, "preexisting_run_metadata_refreshed": refreshed, "current_valuation_inputs_unchanged": true, "reviewed_source_values": 7, "scope": "single_company_cropped_evidence_import_not_market_sync"}
	raw, err = json.MarshalIndent(receipt, "", "  ")
	check(err)
	check(os.WriteFile(filepath.Join(output, "acceptance.json"), raw, 0644))
	t.Logf("cash history standard chain: %+v", receipt)
}
