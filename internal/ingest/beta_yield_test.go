package ingest

import (
	"context"
	"database/sql"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"
)

func TestBetaYieldRealArchiveReplay(t *testing.T) {
	python := os.Getenv("ALPHALAKE_TEST_PYTHON")
	if python == "" {
		t.Skip("CI explicitly runs after installing Python dependencies")
	}
	for _, tc := range []struct {
		name, fixture, script, table, source string
		count                                int
		sync                                 func(context.Context, *sql.DB, string, ReferenceOptions) (ReferenceSummary, error)
	}{
		{"beta", "../source/damodaran/testdata/betaGlobal.xls", "../../valuation/backend/data_sources/damodaran_parsers/beta_parser.py", "reference.industry_stat", "damodaran", 376, SyncIndustryBeta},
		{"credit", "../source/damodaran/testdata/ratings.html", "../source/damodaran/ratings.py", "reference.credit_spread_band", "damodaran", 15, SyncCreditSpreads},
		{"fx", "../source/safe/testdata/rates.html", "../source/safe/parse.py", "market.fx_rate", "safe", 10, SyncHKDCNY},
		{"yield", "../source/chinabond/testdata/curve.html", "../source/chinabond/parse.py", "market.yield_curve_point", "chinabond", 8, SyncCNYGovernmentYield},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ctx := t.Context()
			dir := t.TempDir()
			dbPath := filepath.Join(dir, "reference.duckdb")
			db, err := duckstore.OpenAndMigrate(ctx, dbPath)
			if err != nil {
				t.Fatal(err)
			}
			defer func() {
				if db != nil {
					db.Close()
				}
			}()
			raw, err := os.ReadFile(tc.fixture)
			if err != nil {
				t.Fatal(err)
			}
			var bad atomic.Bool
			var calls atomic.Int32
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				if bad.Load() {
					w.Write([]byte("broken source"))
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
			script, err := filepath.Abs(tc.script)
			if err != nil {
				t.Fatal(err)
			}
			opts := ReferenceOptions{Python: python, Script: script, Client: client}
			root := filepath.Join(dir, "raw")
			first, err := tc.sync(ctx, db, root, opts)
			if err != nil || !first.Inserted || first.Observations != tc.count {
				t.Fatal(first, err)
			}
			replay, err := tc.sync(ctx, db, root, opts)
			if err != nil || replay.Inserted || replay.ReleaseID != first.ReleaseID {
				t.Fatal(replay, err)
			}
			bad.Store(true)
			failure, err := tc.sync(ctx, db, root, opts)
			if err == nil {
				t.Fatal("broken source accepted")
			}
			opts.Offline = true
			replay, err = tc.sync(ctx, db, root, opts)
			if err != nil || replay.Inserted || replay.ReleaseID != first.ReleaseID {
				t.Fatal(replay, err)
			}
			if calls.Load() != 3 {
				t.Fatal("offline HTTP", calls.Load())
			}
			if err := db.Close(); err != nil {
				t.Fatal(err)
			}
			db, err = duckstore.Open(ctx, dbPath)
			if err != nil {
				t.Fatal(err)
			}
			var rows, releases, checks, failed, diagnostics int
			err = db.QueryRowContext(ctx, `SELECT (SELECT count(*) FROM `+tc.table+`),(SELECT count(*) FROM meta.dataset_release),
   (SELECT count(*) FROM meta.checkpoint WHERE source=?),(SELECT count(*) FROM meta.ingest_run WHERE ingest_run_id=? AND status='failed'),
   (SELECT count(*) FROM meta.validation_result WHERE ingest_run_id=? AND rule_code='reference_publication_failed')`, tc.source, failure.RunID, failure.RunID).Scan(&rows, &releases, &checks, &failed, &diagnostics)
			if err != nil || rows != tc.count || releases != 1 || checks != 1 || failed != 1 || diagnostics != 1 {
				t.Fatal(rows, releases, checks, failed, diagnostics, err)
			}
			if tc.name == "beta" {
				var nodes, memberships int
				err = db.QueryRowContext(ctx, `SELECT (SELECT count(*) FROM classification.node),(SELECT count(*) FROM classification.membership)`).Scan(&nodes, &memberships)
				if err != nil || nodes != 94 || memberships != 0 {
					t.Fatal("industry catalog must not assign companies", nodes, memberships, err)
				}
			}
		})
	}
}
