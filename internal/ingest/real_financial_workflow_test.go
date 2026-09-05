package ingest

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/artifact"
	"github.com/yinhm/alphalake/internal/domain"
	"github.com/yinhm/alphalake/internal/source/cninfo"
	"github.com/yinhm/alphalake/internal/source/tdx"
	"github.com/yinhm/alphalake/internal/source/tdx/financial"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

// Replay captured HTTP bytes through the production client into a fresh DB.
// Optional PDFs come from a read-only local archive; this test never calls upstream.
func TestRealFinancialWorkflow(t *testing.T) {
	ctx := t.Context()
	check := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	dbPath := filepath.Join(t.TempDir(), "acceptance.duckdb")
	db, err := duckstore.OpenAndMigrate(ctx, dbPath)
	check(err)
	defer func() { db.Close() }()
	root := filepath.Join(t.TempDir(), "raw")
	count := func(query string, want int) {
		t.Helper()
		var got int
		check(db.QueryRowContext(ctx, query).Scan(&got))
		if got != want {
			t.Fatalf("%s: got %d, want %d", query, got, want)
		}
	}
	var instruments []domain.InstrumentObservation
	check(json.Unmarshal(readAnnualSample(t, "instruments.json"), &instruments))
	_, err = duckstore.UpsertInstruments(ctx, db, instruments)
	check(err)
	count("SELECT count(*) FROM ref.instrument", 7)

	pages := map[string][]byte{}
	for i := 1; i <= 3; i++ {
		pages[strconv.Itoa(i)] = readAnnualSample(t, fmt.Sprintf("page-%d.json", i))
	}
	documents := map[string][]byte{}
	if archive := os.Getenv("ALPHALAKE_ACCEPTANCE_RAW"); archive != "" {
		var inventory []struct {
			URL    string `json:"source_locator"`
			Path   string `json:"local_path"`
			SHA256 string `json:"sha256"`
			Size   int    `json:"content_length"`
		}
		check(json.Unmarshal(readAnnualSample(t, "documents.json"), &inventory))
		for _, doc := range inventory {
			raw, err := os.ReadFile(filepath.Join(archive, doc.Path))
			check(err)
			if len(raw) != doc.Size || fmt.Sprintf("%x", sha256.Sum256(raw)) != doc.SHA256 {
				t.Fatalf("PDF archive integrity mismatch: %s", doc.Path)
			}
			u, err := url.Parse(doc.URL)
			check(err)
			documents[u.Path] = raw
		}
		if len(documents) != 6 {
			t.Fatal("expected six archived PDFs")
		}
	} else {
		t.Log("正文归档校验未启用；设置 ALPHALAKE_ACCEPTANCE_RAW 可校验六份真实 PDF")
	}
	var failSecondPage atomic.Bool
	var requests atomic.Int32
	failSecondPage.Store(true)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		if r.URL.Path == "/new/hisAnnouncement/query" {
			if err := r.ParseForm(); err != nil || r.Form.Get("seDate") != "2026-03-06~2026-03-06" || r.Form.Get("pageSize") != "5" {
				http.Error(w, "unexpected sample query", http.StatusBadRequest)
				return
			}
			page := r.Form.Get("pageNum")
			if page == "2" && failSecondPage.Load() {
				http.Error(w, "injected interruption", http.StatusServiceUnavailable)
				return
			}
			if raw, ok := pages[page]; ok {
				w.Header().Set("Content-Type", "application/json")
				_, _ = w.Write(raw)
				return
			}
		} else if raw, ok := documents[r.URL.Path]; ok {
			w.Header().Set("Content-Type", "application/pdf")
			_, _ = w.Write(raw)
			return
		}
		http.NotFound(w, r)
	}))
	defer server.Close()
	client, err := cninfo.NewClient(server.Client(), cninfo.ClientOptions{BaseURL: server.URL, DocumentBaseURL: server.URL + "/"})
	check(err)
	day := time.Date(2026, 3, 6, 0, 0, 0, 0, time.UTC)
	observed := time.Date(2026, 9, 5, 7, 0, 0, 0, time.UTC)
	options := CNINFOFilingOptions{StartDate: day, EndDate: day, PageSize: 5, MetadataOnly: true, Now: func() time.Time { return observed }}
	interrupted, err := SyncCNINFOFilingsWithOptions(ctx, db, client, root, options)
	if err == nil || interrupted.Pages != 1 || len(interrupted.Failures) != 1 {
		t.Fatalf("expected page-two failure: %+v, %v", interrupted, err)
	}
	count("SELECT count(*) FROM meta.checkpoint WHERE source='cninfo'", 0)
	count("SELECT count(*) FROM fundamental.filing", 5)
	count(fmt.Sprintf("SELECT count(*) FROM meta.ingest_run WHERE ingest_run_id=%d AND status IN ('partial','failed')", interrupted.RunID), 1)
	check(db.Close())
	db, err = duckstore.OpenAndMigrate(ctx, dbPath)
	check(err)
	failSecondPage.Store(false)
	recovered, err := SyncCNINFOFilingsWithOptions(ctx, db, client, root, options)
	check(err)
	if recovered.Pages != 3 || recovered.Filings != 12 || recovered.Pending != 0 || recovered.Issues != 0 {
		t.Fatalf("incomplete recovery: %+v", recovered)
	}
	count("SELECT count(*) FROM fundamental.filing", 12)
	count("SELECT count(*) FROM meta.checkpoint WHERE source='cninfo'", 1)
	count("SELECT count(*) FROM fundamental.filing WHERE resolution_status='pending'", 0)
	count("SELECT count(*) FROM fundamental.filing WHERE announcement_date=DATE '2026-03-06' AND announcement_time_precision='date' AND announcement_time=TIMESTAMPTZ '2026-03-06 16:00:00+00'", 12)
	if len(documents) != 0 {
		// Metadata completion must not suppress the subsequent full-document run.
		options.MetadataOnly = false
		full, err := SyncCNINFOFilingsWithOptions(ctx, db, client, root, options)
		check(err)
		if full.Pages != 3 || full.Documents != 6 || full.Pending != 0 {
			t.Fatalf("full-document mode: %+v", full)
		}
		count("SELECT count(*) FROM fundamental.filing_document", 6)
		count("SELECT count(*) FROM meta.checkpoint WHERE source='cninfo'", 2)
		options.Rescan = true
		rescan, err := SyncCNINFOFilingsWithOptions(ctx, db, client, root, options)
		check(err)
		if rescan.ReusedDocs != 6 || rescan.Documents != 0 {
			t.Fatalf("document reuse: %+v", rescan)
		}
		options.Rescan = false
	}
	before := requests.Load()
	replay, err := SyncCNINFOFilingsWithOptions(ctx, db, client, root, options)
	check(err)
	if replay.SkippedWindows != 1 || replay.Pages != 0 || requests.Load() != before {
		t.Fatalf("checkpoint replay made HTTP requests: %+v", replay)
	}

	// This is a six-company slice, never a completed full upstream package.
	raw := readAnnualSample(t, "gpcw20251231.zip")
	runID, err := duckstore.StartIngestRun(ctx, db, "tdx", "acceptance_sample", nil)
	check(err)
	stored, err := artifact.Persist(ctx, db, root, artifact.Input{
		Source: "tdx", Dataset: "acceptance_sample", SourceLocator: "sample/gpcw20251231.zip",
		FetchedAt: observed, MediaType: "application/zip", ParserVersion: "gpcw-v1", IngestRunID: &runID, Content: raw,
	})
	check(err)
	records, err := (&tdx.Client{}).NormalizeProfessionalFinancialPackage(financial.FileEntry{Filename: "gpcw20251231.zip"}, raw, stored.ArtifactID)
	check(err)
	resolved, resolutions, err := resolveProviderFinancialRecords(ctx, db, records)
	check(err)
	resolution, err := duckstore.ApplyProviderFinancialResolutions(ctx, db, runID, resolutions)
	check(err)
	if len(resolved) != 6 || resolution.Pending != 0 {
		t.Fatalf("financial identity resolution: %+v", resolution)
	}
	first, err := duckstore.ReconcileProviderFinancialRecordsForArtifact(ctx, db, runID, "tdx", stored.SHA256, resolved)
	check(err)
	if first.Inserted != 3504 {
		t.Fatalf("provider facts: %+v", first)
	}
	second, err := duckstore.ReconcileProviderFinancialRecordsForArtifact(ctx, db, runID, "tdx", stored.SHA256, resolved)
	check(err)
	if second.Inserted != 0 || second.Reassigned != 0 || second.Removed != 0 {
		t.Fatalf("non-idempotent provider replay: %+v", second)
	}
	check(duckstore.FinishIngestRun(ctx, db, runID, duckstore.IngestRunCompleted, nil, nil))
	count("SELECT count(*) FROM meta.checkpoint WHERE source='tdx'", 0)
	count("SELECT count(*) FROM fundamental.provider_fact WHERE announcement_time IS NULL", 3504)
	materialized, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if materialized.Inserted != 54 || materialized.Linked != 6 || materialized.Rejected != 0 || materialized.LinkPending != 0 || materialized.LinkAmbiguous != 0 {
		t.Fatalf("materialization coverage: %+v", materialized)
	}
	for _, row := range annualReportValues(t) {
		var value float64
		var period, unit, filingID string
		check(db.QueryRowContext(ctx, `SELECT cast(f.value AS DOUBLE), f.period_type, f.unit, a.source_filing_id
			FROM fundamental.fact f JOIN fundamental.filing a ON a.filing_id=f.source_filing_id
			WHERE f.provider_code=? AND f.source_provider_field=? AND f.report_period=cast(? AS DATE)`, row[0], row[1], row[2]).Scan(&value, &period, &unit, &filingID))
		want, err := strconv.ParseFloat(row[5], 32)
		check(err)
		if value != want || period != row[3] || unit != row[4] || !strings.HasSuffix(row[7], "/"+filingID+".PDF") {
			t.Fatalf("%s/%s: value=%v period=%s unit=%s filing=%s", row[0], row[1], value, period, unit, filingID)
		}
	}
	count("SELECT count(*) FROM fundamental.fact WHERE source_provider_field<>'FN238' AND period_type='Q4' AND unit='CNY'", 48)
	count("SELECT count(*) FROM fundamental.fact WHERE source_provider_field='FN238' AND period_type='FY' AND unit='share'", 6)
	count("SELECT count(*) FROM fundamental.fact_asof(TIMESTAMPTZ '2026-03-06 15:59:59+00')", 0)
	count("SELECT count(*) FROM fundamental.fact_asof(TIMESTAMPTZ '2026-03-06 16:00:00+00')", 54)
	again, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if again.Inserted != 0 || again.Updated != 0 || again.Removed != 0 || again.Rejected != 0 {
		t.Fatalf("non-idempotent materialization: %+v", again)
	}
	count("SELECT count(*) FROM fundamental.fact", 54)
	t.Logf("真实样本验收通过：公告=12，目录页=3，正文=%d，源事实=3504，标准事实=54，独立金额=16；失败恢复、幂等及 PIT 边界通过", len(documents))
}
