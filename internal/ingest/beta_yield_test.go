package ingest

import (
	"bytes"
	"compress/gzip"
	"context"
	"database/sql"
	"encoding/json"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"
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
		{"companies", "../source/damodaran/testdata/indname.xls.gz", "../../valuation/backend/data_sources/damodaran_parsers/company_industry_parser.py", "reference.security_industry", "damodaran", 5100, SyncCompanyIndustries},
		{"capital", "../source/damodaran/testdata/capexGlobal.xls", "../../valuation/backend/data_sources/damodaran_parsers/capex_parser.py", "reference.industry_stat", "damodaran", 94, SyncIndustryCapital},
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
			if tc.name == "companies" {
				z, e := gzip.NewReader(bytes.NewReader(raw))
				if e != nil {
					t.Fatal(e)
				}
				raw, e = io.ReadAll(z)
				z.Close()
				if e != nil {
					t.Fatal(e)
				}
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
			if tc.name == "beta" || tc.name == "capital" || tc.name == "companies" {
				var nodes, memberships int
				err = db.QueryRowContext(ctx, `SELECT (SELECT count(*) FROM classification.node),(SELECT count(*) FROM classification.membership)`).Scan(&nodes, &memberships)
				if err != nil || nodes != 94 || memberships != 0 {
					t.Fatal("industry catalog must not assign companies", nodes, memberships, err)
				}
			}
			if tc.name == "companies" {
				var undated, rawZero int
				err = db.QueryRowContext(ctx, `SELECT (SELECT count(*) FROM meta.dataset_release WHERE source_version IS NULL AND source_published_at IS NULL AND publication_precision='unknown' AND available_at=first_seen_at),
                 (SELECT count(*) FROM reference.security_industry WHERE exchange_ticker='SHSE:601888' AND json_type(CAST(raw_payload AS JSON),'$.sic_code')='DOUBLE' AND json_extract_string(raw_payload,'$.sic_code')='0.0')`).Scan(&undated, &rawZero)
				if err != nil || undated != 1 || rawZero != 1 {
					t.Fatal("source date/type invented", undated, rawZero, err)
				}
				var id, node int64
				err = db.QueryRowContext(ctx, `SELECT observation_id,industry_node_id FROM reference.security_industry WHERE exchange_ticker='SZSE:300866'`).Scan(&id, &node)
				if err != nil {
					t.Fatal(err)
				}
				if _, err = db.ExecContext(ctx, `UPDATE reference.security_industry SET industry_node_id=-1 WHERE observation_id=?`, id); err != nil {
					t.Fatal(err)
				}
				if _, err = tc.sync(ctx, db, root, opts); err == nil {
					t.Fatal("changed industry accepted")
				}
				if _, err = db.ExecContext(ctx, `UPDATE reference.security_industry SET industry_node_id=? WHERE observation_id=?`, node, id); err != nil {
					t.Fatal(err)
				}
				if _, err = db.ExecContext(ctx, `UPDATE meta.checkpoint SET checkpoint_value='invented-version'`); err != nil {
					t.Fatal(err)
				}
				if _, err = tc.sync(ctx, db, root, opts); err == nil {
					t.Fatal("changed completion checkpoint accepted")
				}
				if _, err = db.ExecContext(ctx, `UPDATE meta.checkpoint SET checkpoint_value=CAST(? AS VARCHAR)`, first.ReleaseID); err != nil {
					t.Fatal(err)
				}
				if _, err = tc.sync(ctx, db, root, opts); err != nil {
					t.Fatal("restored evidence cannot replay", err)
				}
				var broken int64
				err = db.QueryRowContext(ctx, `INSERT INTO meta.dataset_release(source,dataset,content_key,publication_precision,available_at,availability_basis,first_seen_at,parser_version,normalization_version,ingest_run_id)
                  SELECT source,dataset,repeat('f',64),publication_precision,available_at,availability_basis,first_seen_at,parser_version,normalization_version,ingest_run_id FROM meta.dataset_release WHERE release_id=? RETURNING release_id`, first.ReleaseID).Scan(&broken)
				if err != nil {
					t.Fatal(err)
				}
				if _, err = tc.sync(ctx, db, root, opts); err == nil {
					t.Fatal("offline silently fell back from broken latest release")
				}
				if _, err = db.ExecContext(ctx, `DELETE FROM meta.dataset_release WHERE release_id=?`, broken); err != nil {
					t.Fatal(err)
				}
			}
			if tc.name == "capital" {
				at := time.Now().UTC()
				fixed, err := duckstore.ExportIndustryCapital(ctx, db, at, nil, first.ReleaseID)
				if err != nil {
					t.Fatal(err)
				}
				latest, err := duckstore.ExportIndustryCapital(ctx, db, at, nil, 0)
				if err != nil {
					t.Fatal(err)
				}
				a, _ := json.Marshal(fixed)
				b, _ := json.Marshal(latest)
				if string(a) != string(b) {
					t.Fatal("latest differs from fixed capital release")
				}
				var broken int64
				err = db.QueryRowContext(ctx, `INSERT INTO meta.dataset_release(source,dataset,source_version,content_key,publication_precision,available_at,availability_basis,first_seen_at,parser_version,normalization_version,ingest_run_id)
                  SELECT source,dataset,'2026-01-06',repeat('f',64),publication_precision,available_at,availability_basis,first_seen_at,parser_version,normalization_version,ingest_run_id FROM meta.dataset_release WHERE release_id=? RETURNING release_id`, first.ReleaseID).Scan(&broken)
				if err != nil {
					t.Fatal(err)
				}
				if _, err = duckstore.ExportIndustryCapital(ctx, db, at, nil, 0); err == nil {
					t.Fatal("broken newer release silently fell back")
				}
				if _, err = duckstore.ExportIndustryCapital(ctx, db, at, nil, first.ReleaseID); err != nil {
					t.Fatal("valid explicit old release unavailable", err)
				}
				if _, err = db.ExecContext(ctx, `DELETE FROM meta.dataset_release WHERE release_id=?`, broken); err != nil {
					t.Fatal(err)
				}
				before := time.Date(2020, 1, 1, 0, 0, 0, 0, time.UTC)
				if _, err = duckstore.ExportIndustryCapital(ctx, db, before, nil, 0); err == nil {
					t.Fatal("future capital publication accepted")
				}
				if _, err = duckstore.ExportIndustryCapital(ctx, db, at, &before, 0); err == nil {
					t.Fatal("future capital recording accepted")
				}
				if _, err = duckstore.ExportIndustryCapital(ctx, db, at, nil, first.ReleaseID+100); err == nil {
					t.Fatal("unknown capital release accepted")
				}
				if _, err = db.ExecContext(ctx, `UPDATE reference.industry_stat SET raw_unit='percent' WHERE observation_id=(SELECT min(observation_id) FROM reference.industry_stat)`); err != nil {
					t.Fatal(err)
				}
				if _, err = duckstore.ExportIndustryCapital(ctx, db, at, nil, 0); err == nil {
					t.Fatal("corrupt selected capital release accepted")
				}
				if _, err = db.ExecContext(ctx, `UPDATE reference.industry_stat SET raw_unit='dimensionless'`); err != nil {
					t.Fatal(err)
				}
				if _, err = db.ExecContext(ctx, `DELETE FROM meta.checkpoint`); err != nil {
					t.Fatal(err)
				}
				if _, err = duckstore.ExportIndustryCapital(ctx, db, at, nil, 0); err == nil {
					t.Fatal("broken capital completion key accepted")
				}
			}

		})
	}
}
