//go:build full

package ingest

import (
	"bytes"
	"compress/gzip"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/yinhm/alphalake/internal/source/macro"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

type macroFixtureTransport func(*http.Request) (*http.Response, error)

func (f macroFixtureTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestMacroRealArchivesReplayAndFailedRunSurviveReopen(t *testing.T) {
	var entries []struct{ Dataset, URL string }
	raw, err := os.ReadFile("../source/macro/testdata/sources.json")
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(raw, &entries); err != nil {
		t.Fatal(err)
	}
	pages := map[string][]byte{}
	nbs := strings.Builder{}
	var moneyURL, flowURL, stockURL, financialURL, fiscalURL string
	for _, entry := range entries {
		packed, err := os.ReadFile("../source/macro/testdata/" + entry.Dataset + ".html.gz")
		if err != nil {
			t.Fatal(err)
		}
		z, err := gzip.NewReader(bytes.NewReader(packed))
		if err != nil {
			t.Fatal(err)
		}
		body, err := io.ReadAll(z)
		z.Close()
		if err != nil {
			t.Fatal(err)
		}
		pages[entry.URL] = body
		feed, _ := macro.FindFeed(entry.Dataset)
		snapshot, err := macro.Parse(body, feed, entry.URL)
		if err != nil {
			t.Fatal(err)
		}
		if feed.Source == "nbs" {
			fmt.Fprintf(&nbs, `<a href="%s">%s</a>`, entry.URL, snapshot.Title)
		}
		switch entry.Dataset {
		case "money":
			moneyURL = entry.URL
		case "afre_flow":
			flowURL = entry.URL
		case "afre_stock":
			stockURL = entry.URL
		case "financial_report":
			financialURL = entry.URL
		case "fiscal":
			fiscalURL = entry.URL
		}
	}
	pages["https://www.stats.gov.cn/sj/zxfb/"] = []byte(nbs.String())
	const root = "https://www.pbc.gov.cn/diaochatongjisi/116219/116319/"
	pages[root+"index.html"] = []byte(`<a href="2026ntjsj/index.html">2026年统计数据</a><a href="2026ntjsj/hbtjgl/index.html">货币统计概览</a><a href="2026ntjsj/shrzgm/index.html">社会融资规模</a>`)
	pages[root+"2026ntjsj/hbtjgl/index.html"] = []byte(`<table><tr><td>货币供应量</td><td><a href="` + moneyURL + `">htm</a></td></tr></table>`)
	pages[root+"2026ntjsj/shrzgm/index.html"] = []byte(`<table><tr><td>社会融资规模增量统计表</td><td><a href="` + flowURL + `">htm</a></td></tr></table><table><tr><td>社会融资规模存量统计表</td><td><a href="` + stockURL + `">htm</a></td></tr></table>`)
	pages["https://www.pbc.gov.cn/diaochatongjisi/116219/116225/index.html"] = []byte(`<a href="` + financialURL + `">2026年前三季度金融统计数据报告</a>`)
	pages["https://gks.mof.gov.cn/tongjishuju/"] = []byte(`<a href="` + fiscalURL + `">2026年1-8月财政收支情况</a>`)
	calls := 0
	client := macro.NewClient()
	client.HTTP.Transport = macroFixtureTransport(func(r *http.Request) (*http.Response, error) {
		calls++
		body, ok := pages[r.URL.String()]
		if !ok {
			t.Errorf("unexpected HTTP %s", r.URL)
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(bytes.NewReader(body)), Header: make(http.Header), Request: r}, nil
	})
	dir := t.TempDir()
	path := filepath.Join(dir, "macro.duckdb")
	db, err := duckstore.OpenInitialized(t.Context(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { db.Close() }()
	options := MacroOptions{Client: client}
	first, err := SyncMacroData(t.Context(), db, dir, options)
	if err != nil || first.Completed != len(macro.Feeds()) || first.Status != "completed" || first.NewObservations < 700 {
		t.Fatal(first, err)
	}
	before := calls
	options.Offline = true
	second, err := SyncMacroData(t.Context(), db, dir, options)
	if err != nil || second.NewObservations != 0 || second.Completed != first.Completed || calls != before {
		t.Fatal(second, err, "HTTP", before, calls)
	}
	for i, r := range first.Datasets {
		if r.ReleaseID != second.Datasets[i].ReleaseID {
			t.Fatal("offline release changed")
		}
	}
	var local string
	if err = db.QueryRow(`SELECT a.local_path FROM meta.dataset_release r JOIN meta.dataset_release_artifact link USING(release_id) JOIN meta.artifact a USING(artifact_id) WHERE r.dataset='macro_cpi_v1'`).Scan(&local); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(local, "objects/") {
		t.Fatal("macro archive did not use flat source layout", local)
	}
	if err = os.WriteFile(filepath.Join(dir, filepath.FromSlash(local)), []byte("corrupt fixture"), 0600); err != nil {
		t.Fatal(err)
	}
	failed, err := SyncMacroData(t.Context(), db, dir, options)
	if err == nil || failed.Status != "partial" || failed.Completed != first.Completed-1 || calls != before {
		t.Fatal(failed, err)
	}
	var failedRun int64
	for _, r := range failed.Datasets {
		if r.Dataset == "cpi" {
			failedRun = r.RunID
		}
	}
	if err = db.Close(); err != nil {
		t.Fatal(err)
	}
	db, err = duckstore.OpenInitialized(t.Context(), path)
	if err != nil {
		t.Fatal(err)
	}
	var releases, points, retainedFailure int
	if err = db.QueryRow(`SELECT (SELECT count(*) FROM meta.dataset_release),(SELECT count(*) FROM macro.observation),(SELECT count(*) FROM meta.ingest_run WHERE ingest_run_id=? AND status='failed' AND error_message IS NOT NULL)`, failedRun).Scan(&releases, &points, &retainedFailure); err != nil || releases != len(macro.Feeds()) || points != first.NewObservations || retainedFailure != 1 {
		t.Fatal(releases, points, retainedFailure, err)
	}
	if err = db.QueryRow(`SELECT count(*) FROM meta.artifact WHERE source='mof' AND dataset='macro_fiscal_v1' AND local_path LIKE 'mof/macro_fiscal_v1-%'`).Scan(&retainedFailure); err != nil || retainedFailure != 1 {
		t.Fatal(retainedFailure, err)
	}
}
