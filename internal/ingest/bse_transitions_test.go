package ingest

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	"github.com/yinhm/alphalake/internal/source/bse"
	"github.com/yinhm/alphalake/internal/source/cninfo"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestBSETransitionPublicationRealEvidence(t *testing.T) {
	python := os.Getenv("ALPHALAKE_TEST_PYTHON")
	if python == "" {
		t.Skip("mandatory Python source check in CI")
	}
	bodies := map[string][]byte{}
	for role, location := range bse.URLs {
		u, err := url.Parse(location)
		if err != nil {
			t.Fatal(err)
		}
		bodies[u.Path], err = os.ReadFile(filepath.Join("testdata", "bse-code-transition-2025", role+".html"))
		if err != nil {
			t.Fatal(err)
		}
	}
	var bad, revision atomic.Bool
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.UserAgent() != "AlphaLake/1.0" || r.Header.Get("Accept") != "text/html" {
			w.WriteHeader(403)
			return
		}
		body, ok := bodies[r.URL.Path]
		if !ok {
			http.NotFound(w, r)
			return
		}
		if bad.Load() && r.URL.Path == "/important_news/200025603.html" {
			w.WriteHeader(503)
			return
		}
		if revision.Load() && r.URL.Path == "/important_news/200026735.html" {
			body = append(bytes.Clone(body), '\n')
		}
		w.Write(body)
	}))
	defer server.Close()
	endpoint, _ := url.Parse(server.URL)
	client := server.Client()
	client.Transport = countryTransport(func(r *http.Request) (*http.Response, error) {
		clone := r.Clone(r.Context())
		u := *r.URL
		u.Scheme, u.Host = endpoint.Scheme, endpoint.Host
		clone.URL = &u
		return http.DefaultTransport.RoundTrip(clone)
	})
	ctx := t.Context()
	dir := t.TempDir()
	path := filepath.Join(dir, "bse.duckdb")
	root := filepath.Join(dir, "raw")
	db, err := duckstore.OpenAndMigrate(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if db != nil {
			db.Close()
		}
	}()
	options := ReferenceOptions{Python: python, Script: "../source/bse/parse.py", Client: client}
	first, err := SyncBSECodeTransitions(ctx, db, root, options)
	if err != nil || !first.Inserted || first.Observations != 248 || calls.Load() != 4 {
		t.Fatal(first, err, calls.Load())
	}
	var complete bool
	err = db.QueryRowContext(ctx, `SELECT r.available_at=max(a.fetched_at) AND r.first_seen_at=max(a.fetched_at) AND min(a.fetched_at)<max(a.fetched_at) AND r.source_version IS NULL AND count(*)=4
 FROM meta.dataset_release r JOIN meta.dataset_release_artifact l USING(release_id) JOIN meta.artifact a USING(artifact_id)
 WHERE r.release_id=? GROUP BY r.available_at,r.first_seen_at,r.source_version`, first.ReleaseID).Scan(&complete)
	if err != nil || !complete {
		t.Fatal("four-file availability boundary lost", complete, err)
	}

	// 只有新代码的标准标识；旧公告通过官方切换边界锚定同一ID，不新增旧区间。
	if _, err = db.ExecContext(ctx, `INSERT INTO core.instrument(instrument_id,instrument_type,exchange_mic,currency) VALUES (9007199254740993,'equity','XBSE','CNY'),(2,'equity','XBSE','CNY'),(9,'equity','XBSE','CNY')`); err != nil {
		t.Fatal(err)
	}
	if _, err = db.ExecContext(ctx, `INSERT INTO core.instrument_identifier(instrument_id,provider,identifier_type,identifier_value,valid_from) VALUES (9007199254740993,'tdx','symbol','bj920819','2025-05-06'),(2,'tdx','symbol','bj920123','2025-10-09')`); err != nil {
		t.Fatal(err)
	}
	var realFilings []domain.FilingObservation
	for _, code := range []string{"920819", "920123"} {
		body, e := os.ReadFile(filepath.Join("testdata", "bse-code-transition-2025", code+"-catalogue.json"))
		if e != nil {
			t.Fatal(e)
		}
		page, e := cninfo.ParseCataloguePage(body)
		if e != nil {
			t.Fatal(e)
		}
		if e = duckstore.ValidateCNINFOCodeQuery(ctx, db, code, page.Filings[0].ProviderOrgID, page.Filings); e != nil {
			t.Fatal(e)
		}
		resolved, e := duckstore.ResolveFilingObservations(ctx, db, page.Filings)
		if e != nil {
			t.Fatal(e)
		}
		expected := int64(2)
		if code == "920819" {
			expected = 9007199254740993
		}
		for i, f := range resolved {
			if f.InstrumentID != expected || f.ProviderCode != page.Filings[i].ProviderCode || !strings.HasPrefix(f.ResolutionReason, "bse-transition-v1:") {
				t.Fatal("identity rewrite or missing lineage", f)
			}
		}
		realFilings = append(realFilings, resolved...)
	}
	if _, err = duckstore.UpsertFilings(ctx, db, first.RunID, realFilings); err != nil {
		t.Fatal(err)
	}
	refresh := func(wantResolved int) {
		t.Helper()
		r, e := duckstore.RefreshPendingFilingResolutions(ctx, db, first.RunID, 7)
		if e != nil || r.Attempted != 27 || r.Resolved != wantResolved || r.StillPending != 27-wantResolved {
			t.Fatal("persisted identity refresh", r, e)
		}
		var resolved, pending int
		if e = db.QueryRowContext(ctx, `SELECT count(*) FILTER (WHERE resolution_status='resolved' AND instrument_id IS NOT NULL),count(*) FILTER (WHERE resolution_status='pending' AND instrument_id IS NULL) FROM fundamental.filing`).Scan(&resolved, &pending); e != nil || resolved != wantResolved || pending != 27-wantResolved {
			t.Fatal("stored identity state", resolved, pending, e)
		}
		for _, original := range realFilings {
			var code string
			var day, available time.Time
			if e = db.QueryRowContext(ctx, `SELECT provider_code,announcement_date,announcement_time FROM fundamental.filing WHERE source_filing_id=?`, original.SourceFilingID).Scan(&code, &day, &available); e != nil || code != original.ProviderCode || !day.Equal(original.AnnouncementDate) || !available.Equal(original.AnnouncementTime) {
				t.Fatal("refresh rewrote source identity or time", code, day, available, e)
			}
		}
	}
	refresh(27)
	rechecked, e := duckstore.RefreshPendingFilingResolutions(ctx, db, first.RunID, 7)
	if e != nil || rechecked.Resolved != 27 || rechecked.Recovered != 0 {
		t.Fatal("revalidation counted as newly recovered", rechecked, e)
	}
	old := realFilings[15] // 试点切换前的真实旧代码一季报。
	if old.ProviderCode != "833819" {
		t.Fatal(old)
	}
	for _, mutation := range []string{"wrong_org", "wrong_date", "wrong_security", "other_known_id", "overlap", "missing_anchor"} {
		f := old
		switch mutation {
		case "wrong_org":
			f.ProviderOrgID = "other-org"
		case "wrong_date":
			f.AnnouncementDate = time.Date(2025, 5, 6, 0, 0, 0, 0, time.UTC)
		case "wrong_security":
			f.ProviderCode = "837023"
		case "other_known_id":
			_, err = db.ExecContext(ctx, `INSERT INTO core.instrument_identifier(instrument_id,provider,identifier_type,identifier_value) VALUES (9,'tdx','symbol','bj833819')`)
		case "overlap":
			_, err = db.ExecContext(ctx, `INSERT INTO core.instrument_identifier(instrument_id,provider,identifier_type,identifier_value) VALUES (9007199254740993,'tdx','symbol','bj920819')`)
		case "missing_anchor":
			_, err = db.ExecContext(ctx, `UPDATE core.instrument_identifier SET valid_from='2025-05-07' WHERE identifier_value='bj920819'`)
		}
		if err != nil {
			t.Fatal(err)
		}
		if e := duckstore.ValidateCNINFOCodeQuery(ctx, db, "920819", old.ProviderOrgID, []domain.FilingObservation{f}); e == nil {
			t.Fatal("invalid code/identity accepted", mutation)
		}
		if mutation == "other_known_id" {
			_, err = db.ExecContext(ctx, `DELETE FROM core.instrument_identifier WHERE identifier_value='bj833819'`)
		}
		if mutation == "overlap" {
			_, err = db.ExecContext(ctx, `DELETE FROM core.instrument_identifier WHERE identifier_value='bj920819' AND valid_from IS NULL`)
		}
		if mutation == "missing_anchor" {
			_, err = db.ExecContext(ctx, `UPDATE core.instrument_identifier SET valid_from='2025-05-06' WHERE identifier_value='bj920819'`)
		}
		if err != nil {
			t.Fatal(err)
		}
	}
	// 合成金额仅验证身份失效向标准事实传播；不冒充该公司的财务原文验收。
	var financialArtifact int64
	if err = db.QueryRowContext(ctx, `INSERT INTO meta.artifact(source,dataset,source_locator,fetched_at,sha256,content_length) VALUES ('tdx','professional_financial','test/bse-identity.zip',now(),'bse-test-revision',1) RETURNING artifact_id`).Scan(&financialArtifact); err != nil {
		t.Fatal(err)
	}
	if _, err = db.ExecContext(ctx, `INSERT INTO fundamental.provider_fact(instrument_id,source,report_period,provider_code,provider_field,value,artifact_id,revision_key) VALUES (9007199254740993,'tdx','2025-03-31','920819','FN230',100,?,'bse-test-revision')`, financialArtifact); err != nil {
		t.Fatal(err)
	}
	materialize := func(want, inserted, removed int) {
		t.Helper()
		r, e := MaterializeProviderFundamentals(ctx, db, "tdx")
		if e != nil || r.Materialized != want || r.Inserted != inserted || r.Removed != removed {
			t.Fatal("identity-dependent fact lifecycle", r, e)
		}
		var facts, source int
		if e = db.QueryRowContext(ctx, `SELECT (SELECT count(*) FROM fundamental.fact),(SELECT count(*) FROM fundamental.provider_fact WHERE value=100)`).Scan(&facts, &source); e != nil || facts != want || source != 1 {
			t.Fatal("canonical removal/source retention", facts, source, e)
		}
	}
	materialize(1, 1, 0)
	again, err := SyncBSECodeTransitions(ctx, db, root, options)
	if err != nil || again.Inserted || again.ReleaseID != first.ReleaseID || calls.Load() != 8 {
		t.Fatal(again, err)
	}
	bad.Store(true)
	if _, err = SyncBSECodeTransitions(ctx, db, root, options); err == nil {
		t.Fatal("partial evidence published")
	}
	bad.Store(false)
	revision.Store(true)
	second, err := SyncBSECodeTransitions(ctx, db, root, options)
	if err != nil || !second.Inserted || second.ReleaseID == first.ReleaseID || second.ArtifactID != first.ArtifactID {
		t.Fatal("support-only change not versioned", second, err)
	}
	options.Offline = true
	count := calls.Load()
	replay, err := SyncBSECodeTransitions(ctx, db, root, options)
	if err != nil || replay.Inserted || replay.ReleaseID != second.ReleaseID || calls.Load() != count {
		t.Fatal("offline cohort replay failed", replay, err)
	}
	if _, err = db.ExecContext(ctx, `UPDATE reference.security_code_transition SET switch_date='2025-10-08' WHERE release_id=? AND source_row=1`, second.ReleaseID); err != nil {
		t.Fatal(err)
	}
	if _, err = SyncBSECodeTransitions(ctx, db, root, options); err == nil {
		t.Fatal("modified interpretation accepted")
	}
	refresh(0)
	materialize(0, 0, 1)
	invalid, e := duckstore.ResolveFilingObservations(ctx, db, realFilings)
	if e != nil {
		t.Fatal(e)
	}
	for _, f := range invalid {
		if f.InstrumentID != 0 || f.ResolutionStatus != domain.FilingResolutionPending {
			t.Fatal("invalid reference retained identity", f)
		}
	}

	if _, err = db.ExecContext(ctx, `UPDATE reference.security_code_transition SET switch_date='2025-10-09' WHERE release_id=? AND source_row=1`, second.ReleaseID); err != nil {
		t.Fatal(err)
	}
	refresh(27)
	materialize(1, 1, 0)
	materialize(1, 0, 0)
	refresh(27) // 恢复后重复执行不丢失、不增加公告。
	if _, err = db.ExecContext(ctx, `DELETE FROM meta.dataset_release_artifact WHERE release_id=? AND role='timing' AND artifact_id IN (SELECT artifact_id FROM meta.artifact WHERE source_locator=?)`, second.ReleaseID, bse.URLs["pilot-start"]); err != nil {
		t.Fatal(err)
	}
	if _, err = SyncBSECodeTransitions(ctx, db, root, options); err == nil || calls.Load() != count {
		t.Fatal("broken latest cohort fell back or used HTTP", err)
	}
	if err = db.Close(); err != nil {
		t.Fatal(err)
	}
	db = nil
	db, err = duckstore.Open(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	refresh(0) // 重开后仍重新校验；缺少最新证据不回退旧版本。
	var observations, releases, checkpoints, identities, failed int
	err = db.QueryRowContext(ctx, `SELECT (SELECT count(*) FROM reference.security_code_transition),(SELECT count(*) FROM meta.dataset_release),(SELECT count(*) FROM meta.checkpoint),(SELECT count(*) FROM core.instrument_identifier),(SELECT count(*) FROM meta.ingest_run WHERE status='failed')`).Scan(&observations, &releases, &checkpoints, &identities, &failed)
	if err != nil || observations != 496 || releases != 2 || checkpoints != 2 || identities != 2 || failed != 3 {
		t.Fatal("reopen publication/failure state", observations, releases, checkpoints, identities, failed, err)
	}
}
