package ingest

import (
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"

	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

type countryTransport func(*http.Request) (*http.Response, error)

func (f countryTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestCountryRiskRealArchiveReplay(t *testing.T) {
	python := os.Getenv("ALPHALAKE_TEST_PYTHON")
	if python == "" {
		t.Skip("set ALPHALAKE_TEST_PYTHON; CI runs this explicitly after installing valuation dependencies")
	}
	ctx := t.Context()
	dir := t.TempDir()
	dbPath := filepath.Join(dir, "risk.duckdb")
	db, err := duckstore.OpenAndMigrate(ctx, dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if db != nil {
			db.Close()
		}
	}()
	raw, err := os.ReadFile("../source/damodaran/testdata/ctrypremJuly26.xlsx")
	if err != nil {
		t.Fatal(err)
	}
	var calls atomic.Int32
	var bad atomic.Bool
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if bad.Load() {
			w.Write([]byte("not a workbook"))
			return
		}
		w.Write(raw)
	}))
	defer server.Close()
	endpoint, _ := url.Parse(server.URL)
	client := server.Client()
	client.Transport = countryTransport(func(r *http.Request) (*http.Response, error) {
		clone := r.Clone(r.Context())
		u := *r.URL
		u.Scheme = endpoint.Scheme
		u.Host = endpoint.Host
		clone.URL = &u
		return http.DefaultTransport.RoundTrip(clone)
	})
	script, err := filepath.Abs("../../valuation/backend/data_sources/damodaran_parsers/country_risk_parser.py")
	if err != nil {
		t.Fatal(err)
	}
	opts := CountryRiskOptions{Python: python, Script: script, Client: client}
	root := filepath.Join(dir, "raw")
	first, err := SyncCountryRisk(ctx, db, root, opts)
	if err != nil || !first.Inserted || first.Observations != 10 {
		t.Fatal(first, err)
	}
	second, err := SyncCountryRisk(ctx, db, root, opts)
	if err != nil || second.Inserted || second.ReleaseID != first.ReleaseID {
		t.Fatal(second, err)
	}
	if calls.Load() != 2 {
		t.Fatal("HTTP count", calls.Load())
	}
	bad.Store(true)
	failed, err := SyncCountryRisk(ctx, db, root, opts)
	if err == nil {
		t.Fatal("malformed archive accepted")
	}
	opts.Offline = true
	replay, err := SyncCountryRisk(ctx, db, root, opts)
	if err != nil || replay.Inserted || replay.ReleaseID != first.ReleaseID {
		t.Fatal(replay, err)
	}
	if calls.Load() != 3 {
		t.Fatal("offline made an HTTP request")
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	db, err = duckstore.Open(ctx, dbPath)
	if err != nil {
		t.Fatal(err)
	}
	var releases, rows, checkpoints, failedRuns int
	err = db.QueryRowContext(ctx, `SELECT (SELECT count(*) FROM meta.dataset_release),
  (SELECT count(*) FROM reference.country_risk),
  (SELECT count(*) FROM meta.checkpoint WHERE source='damodaran'),
  (SELECT count(*) FROM meta.ingest_run WHERE ingest_run_id=? AND status='failed' AND error_message IS NOT NULL)`, failed.RunID).Scan(&releases, &rows, &checkpoints, &failedRuns)
	if err != nil || releases != 1 || rows != 10 || checkpoints != 1 || failedRuns != 1 {
		t.Fatal(releases, rows, checkpoints, failedRuns, err)
	}
}
