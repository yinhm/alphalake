package ingest

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	"github.com/yinhm/alphalake/internal/source/cninfo"
	"github.com/yinhm/alphalake/internal/source/eastmoney"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestEarningsGuidanceIngestReplayRevisionAndFailure(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "test.duckdb")
	db, err := duckstore.OpenInitialized(t.Context(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { db.Close() }()
	id, err := duckstore.UpsertInstrument(t.Context(), db, domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHE", Currency: "CNY", Name: "鼎龙股份"}, domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sz300054"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = db.Exec(`INSERT INTO fundamental.statement_snapshot(source_record_id,instrument_id,report_period,ingest_run_id,revenue) VALUES(1,?,'2026-06-30',1,123.5)`, id); err != nil {
		t.Fatal(err)
	}
	emRaw, err := os.ReadFile("../source/eastmoney/testdata/dinglong.json")
	if err != nil {
		t.Fatal(err)
	}
	var emDocument map[string]any
	json.Unmarshal(emRaw, &emDocument)
	result := emDocument["result"].(map[string]any)
	result["data"] = result["data"].([]any)[:2]
	result["count"] = 2
	result["pages"] = 1
	ci1, _ := os.ReadFile("../source/cninfo/testdata/guidance/page1.json")
	ci2, _ := os.ReadFile("../source/cninfo/testdata/guidance/page2.json")
	httpCalls := 0
	failure := ""
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		httpCalls++
		if r.Method == http.MethodGet {
			if r.URL.Query().Get("reportName") == "" {
				if failure == "pdf" {
					http.Error(w, "forced PDF failure", http.StatusServiceUnavailable)
				} else {
					w.Header().Set("Content-Type", "application/pdf")
					w.Write([]byte("%PDF-1.4\nsynthetic attachment\n%%EOF"))
				}
				return
			}
			if failure == "eastmoney" {
				w.Write([]byte(`{"success":false,"message":"forced failure"}`))
				return
			}
			json.NewEncoder(w).Encode(emDocument)
			return
		}
		r.ParseForm()
		if r.Form.Get("category") != cninfo.GuidanceCategories {
			t.Error(r.Form)
		}
		page, _ := strconv.Atoi(r.Form.Get("pageNum"))
		if page == 1 || failure == "overlap" {
			w.Write(ci1)
		} else {
			w.Write(ci2)
		}
	}))
	defer server.Close()
	em := &eastmoney.Client{HTTP: server.Client(), Endpoint: server.URL}
	ci, err := cninfo.NewClient(server.Client(), cninfo.ClientOptions{BaseURL: server.URL, DocumentBaseURL: server.URL + "/"})
	if err != nil {
		t.Fatal(err)
	}
	now := time.Date(2026, 10, 9, 12, 0, 0, 0, time.UTC)
	options := GuidanceOptions{Period: "2026-09-30", Start: "2026-09-01", End: "2026-10-09", Scope: []string{"SZSE:300054"}, MetadataOnly: true, Now: func() time.Time { return now }}
	out, err := SyncEarningsGuidance(t.Context(), db, root, em, ci, options)
	if err != nil || out.Sources[0].Inserted != 1 || out.Sources[1].Pages != 2 || out.Relations != 1 {
		t.Fatal(out, err)
	}
	rows, err := duckstore.QueryEarningsGuidance(t.Context(), db, options.Period, options.Scope)
	if err != nil || len(rows) != 2 {
		t.Fatal(rows, err)
	}
	var first string
	var obsID string
	for _, row := range rows {
		if row["source"] == "eastmoney" {
			if row["forecast_parent_net_income_lower"] != "840000000.0000000000" {
				t.Fatal(row)
			}
			first = row["first_observed_at"].(string)
			obsID = row["observation_id"].(string)
		}
	}
	callsBefore := httpCalls
	options.Offline = true
	now = now.Add(time.Hour)
	out, err = SyncEarningsGuidance(t.Context(), db, root, nil, nil, options)
	if err != nil || httpCalls != callsBefore || out.Sources[0].Inserted != 0 || out.Sources[1].Inserted != 0 || out.Relations != 0 {
		t.Fatal(out, err, httpCalls, callsBefore)
	}
	rows, err = duckstore.QueryEarningsGuidance(t.Context(), db, options.Period, options.Scope)
	if err != nil {
		t.Fatal(err)
	}
	for _, row := range rows {
		if row["observation_id"] == obsID && row["first_observed_at"] != first {
			t.Fatal("first observed changed")
		}
	}
	// A same-source new version with NULL must not borrow the earlier interval.
	options.Offline = false
	for _, item := range result["data"].([]any) {
		row := item.(map[string]any)
		if row["PREDICT_FINANCE_CODE"] == "004" {
			row["PREDICT_AMT_LOWER"] = nil
			row["PREDICT_AMT_UPPER"] = nil
		}
	}
	out, err = SyncEarningsGuidance(t.Context(), db, root, em, ci, options)
	if err != nil || out.Sources[0].Inserted != 1 {
		t.Fatal(out, err)
	}
	rows, _ = duckstore.QueryEarningsGuidance(t.Context(), db, options.Period, options.Scope)
	for _, row := range rows {
		if row["source"] == "eastmoney" && row["forecast_parent_net_income_lower"] != nil {
			t.Fatal("old value patched new version", row)
		}
	}
	key := duckstore.GuidanceCheckpointKey(options.Period, options.Start, options.End, options.Scope)
	before, _, err := duckstore.GetCheckpoint(t.Context(), db, cninfo.Source, duckstore.GuidanceDataset, key)
	if err != nil {
		t.Fatal(err)
	}
	failure = "overlap"
	now = now.Add(time.Hour)
	out, err = SyncEarningsGuidance(t.Context(), db, root, em, ci, options)
	if err == nil || out.Sources[0].Status != "completed" || out.Sources[1].Status != "failed" {
		t.Fatal(out, err)
	}
	after, _, _ := duckstore.GetCheckpoint(t.Context(), db, cninfo.Source, duckstore.GuidanceDataset, key)
	if before != after {
		t.Fatal("partial catalogue advanced checkpoint")
	}
	failedRun := out.Sources[1].RunID
	if err = db.Close(); err != nil {
		t.Fatal(err)
	}
	db, err = duckstore.OpenInitialized(t.Context(), path)
	if err != nil {
		t.Fatal(err)
	}
	var status string
	if err = db.QueryRow(`SELECT status FROM meta.ingest_run WHERE ingest_run_id=?`, failedRun).Scan(&status); err != nil || status != "failed" {
		t.Fatal(status, err)
	}
	var revenue float64
	if err = db.QueryRow(`SELECT revenue FROM fundamental.statement_snapshot`).Scan(&revenue); err != nil || revenue != 123.5 {
		t.Fatal(revenue, err)
	}
	// PDF failure does not block independently complete catalogue / structured amounts.
	options.MetadataOnly = false
	failure = "pdf"
	out, err = SyncEarningsGuidance(t.Context(), db, root, em, ci, options)
	if err == nil || out.Sources[0].Status != "completed" || out.Sources[1].Status != "partial" || out.Sources[1].DocumentFailures != 1 {
		t.Fatal(out, err)
	}
	failure = ""
	out, err = SyncEarningsGuidance(t.Context(), db, root, em, ci, options)
	if err != nil || out.Sources[1].Documents != 1 {
		t.Fatal(out, err)
	}
	options.Offline = true
	beforeDocs := httpCalls
	out, err = SyncEarningsGuidance(t.Context(), db, root, nil, nil, options)
	if err != nil || httpCalls != beforeDocs {
		t.Fatal(out, err, httpCalls, beforeDocs)
	}
	options.MetadataOnly = true
	// Corrupt archived bytes are refused; no network repair is attempted offline.
	var local string
	if err = db.QueryRow(`SELECT local_path FROM meta.artifact WHERE source='eastmoney' ORDER BY artifact_id DESC LIMIT 1`).Scan(&local); err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(root, local), []byte("tampered"), 0600); err != nil {
		t.Fatal(err)
	}
	options.Offline = true
	beforeCalls := httpCalls
	if out, err = SyncEarningsGuidance(t.Context(), db, root, nil, nil, options); err == nil || out.Sources[0].Status != "failed" || httpCalls != beforeCalls {
		t.Fatal(out, err)
	}
	// Relational review withdrawal and review revocation leave original amounts intact.
	var target string
	if err = db.QueryRow(`SELECT observation_id FROM fundamental.current_earnings_guidance WHERE source='eastmoney'`).Scan(&target); err != nil {
		t.Fatal(err)
	}
	runID, err := duckstore.StartIngestRun(t.Context(), db, "alphalake", duckstore.GuidanceDataset, nil)
	if err != nil {
		t.Fatal(err)
	}
	relation := domain.GuidanceRelation{FromID: target, ToID: target, Kind: "withdraws", Evidence: "synthetic official withdrawal evidence", Rule: "test-review-v1"}
	if n, err := duckstore.PublishGuidanceRelations(t.Context(), db, runID, []domain.GuidanceRelation{relation}); err != nil || n != 1 {
		t.Fatal(n, err)
	}
	checkLife := func(want string) {
		t.Helper()
		items, e := duckstore.QueryEarningsGuidance(t.Context(), db, options.Period, options.Scope)
		if e != nil {
			t.Fatal(e)
		}
		for _, item := range items {
			if item["observation_id"] == target && item["lifecycle"] != want {
				t.Fatal(item["lifecycle"], want)
			}
		}
	}
	checkLife("withdrawn")
	raw, _ := json.Marshal(relation)
	sum := sha256.Sum256(raw)
	relation.Kind = "revoke"
	relation.RevokesID = fmt.Sprintf("%x", sum)
	relation.Evidence = "synthetic review revocation"
	if n, err := duckstore.PublishGuidanceRelations(t.Context(), db, runID, []domain.GuidanceRelation{relation}); err != nil || n != 1 {
		t.Fatal(n, err)
	}
	if n, err := duckstore.PublishGuidanceRelations(t.Context(), db, runID, []domain.GuidanceRelation{relation}); err != nil || n != 0 {
		t.Fatal(n, err)
	}
	checkLife("current")
	if err = duckstore.FinishIngestRun(t.Context(), db, runID, "completed", nil, nil); err != nil {
		t.Fatal(err)
	}
}

func TestEarningsGuidanceSplitWindowsAndOfflineCoverage(t *testing.T) {
	root := t.TempDir()
	db, err := duckstore.OpenInitialized(t.Context(), filepath.Join(root, "split.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	_, err = duckstore.UpsertInstrument(t.Context(), db, domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHE", Currency: "CNY", Name: "鼎龙股份"}, domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sz300054"})
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile("../source/cninfo/testdata/guidance/page1.json")
	var fixture map[string]any
	if err = json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	var row any
	for _, v := range fixture["announcements"].([]any) {
		if v.(map[string]any)["secCode"] == "300054" {
			row = v
		}
	}
	if row == nil {
		t.Fatal("missing real sample")
	}
	calls := 0
	failLeaf := false
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.Method == http.MethodGet {
			w.Write([]byte(`{"success":true,"result":{"count":0,"data":[]}}`))
			return
		}
		r.ParseForm()
		window := r.Form.Get("seDate")
		count := 1
		rows := []any{row}
		if window == "2026-10-08~2026-10-09" {
			count = 2
		} // Repeated second page triggers a date-window split.
		if window == "2026-10-08~2026-10-08" {
			count = 0
			rows = []any{}
			if failLeaf {
				count = 1
			}
		}
		json.NewEncoder(w).Encode(map[string]any{"announcements": rows, "totalAnnouncement": count, "totalpages": 1})
	}))
	defer server.Close()
	ci, err := cninfo.NewClient(server.Client(), cninfo.ClientOptions{BaseURL: server.URL})
	if err != nil {
		t.Fatal(err)
	}
	em := &eastmoney.Client{HTTP: server.Client(), Endpoint: server.URL}
	options := GuidanceOptions{Period: "2026-09-30", Start: "2026-10-08", End: "2026-10-09", Scope: []string{"SZSE:300054"}, MetadataOnly: true}
	out, err := SyncEarningsGuidance(t.Context(), db, root, em, ci, options)
	if err != nil || out.Sources[1].WindowSplits != 1 || out.Sources[1].Pages != 4 || out.Sources[1].Rows != 1 || out.Sources[1].Inserted != 1 {
		t.Fatal(out, err)
	}
	key := duckstore.GuidanceCheckpointKey(options.Period, options.Start, options.End, options.Scope)
	before, _, err := duckstore.GetCheckpoint(t.Context(), db, cninfo.Source, duckstore.GuidanceDataset, key)
	if err != nil {
		t.Fatal(err)
	}
	var receipt duckstore.GuidanceReceipt
	json.Unmarshal([]byte(before), &receipt)
	if len(receipt.CatalogueWindows) != 2 || len(receipt.ArtifactIDs) != 2 {
		t.Fatal(receipt)
	}
	beforeCalls := calls
	options.Offline = true
	out, err = SyncEarningsGuidance(t.Context(), db, root, nil, nil, options)
	if err != nil || calls != beforeCalls || out.Sources[1].Inserted != 0 || out.Sources[1].Pages != 2 {
		t.Fatal(out, err, calls)
	}
	options.Offline = false
	out, err = SyncEarningsGuidance(t.Context(), db, root, em, ci, options)
	if err != nil || out.Sources[1].Pages != 2 || out.Sources[1].WindowSplits != 0 {
		t.Fatal("did not reuse successful window plan", out, err)
	}
	before, _, _ = duckstore.GetCheckpoint(t.Context(), db, cninfo.Source, duckstore.GuidanceDataset, key)
	failLeaf = true
	out, err = SyncEarningsGuidance(t.Context(), db, root, em, ci, options)
	if err == nil || out.Sources[1].Status != "failed" {
		t.Fatal(out, err)
	}
	after, _, _ := duckstore.GetCheckpoint(t.Context(), db, cninfo.Source, duckstore.GuidanceDataset, key)
	if before != after {
		t.Fatal("incomplete child window advanced parent checkpoint")
	}
	json.Unmarshal([]byte(before), &receipt)
	receipt.CatalogueWindows = receipt.CatalogueWindows[1:]
	broken, _ := json.Marshal(receipt)
	if _, err = db.Exec(`UPDATE meta.checkpoint SET checkpoint_value=? WHERE source='cninfo' AND dataset=? AND checkpoint_key=?`, string(broken), duckstore.GuidanceDataset, key); err != nil {
		t.Fatal(err)
	}
	options.Offline = true
	beforeCalls = calls
	out, err = SyncEarningsGuidance(t.Context(), db, root, nil, nil, options)
	if err == nil || calls != beforeCalls {
		t.Fatal("missing archived child window accepted", out, err)
	}
}

func TestGuidanceDenseRealCataloguePagination(t *testing.T) {
	root := t.TempDir()
	db, err := duckstore.OpenInitialized(t.Context(), filepath.Join(root, "dense.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	unstable := false
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		r.ParseForm()
		p := r.Form.Get("pageNum")
		if r.Form.Get("sortName") != "" || r.Form.Get("sortType") != "" {
			t.Error("time-only sort reintroduced")
		}
		file := "page" + p + ".json"
		if unstable {
			if p == "1" {
				file = "unstable-page3.json"
			} else {
				file = "unstable-page4.json"
			}
		}
		raw, e := os.ReadFile("../source/cninfo/testdata/guidance/dense/" + file)
		if e != nil {
			t.Error(e)
			http.Error(w, "fixture missing", 500)
			return
		}
		w.Write(raw)
	}))
	defer server.Close()
	ci, err := cninfo.NewClient(server.Client(), cninfo.ClientOptions{BaseURL: server.URL})
	if err != nil {
		t.Fatal(err)
	}
	run, err := duckstore.StartIngestRun(t.Context(), db, cninfo.Source, duckstore.GuidanceDataset, nil)
	if err != nil {
		t.Fatal(err)
	}
	options := GuidanceOptions{Period: "2026-06-30", Start: "2026-07-11", End: "2026-07-11"}
	receipt := duckstore.GuidanceReceipt{RunID: run, ObservedAt: time.Now().UTC()}
	summary := GuidanceSourceSummary{}
	_, _, _, err = acquireCNINFOGuidancePages(t.Context(), db, root, ci, options, duckstore.GuidanceReceipt{}, &receipt, &summary)
	if err != nil || summary.Pages != 5 || summary.Rows != 125 || len(receipt.ArtifactIDs) != 5 {
		t.Fatal(summary, receipt, err)
	}
	unstable = true
	summary = GuidanceSourceSummary{}
	receipt.ArtifactIDs = nil
	if _, _, _, err = acquireCNINFOGuidancePages(t.Context(), db, root, ci, options, duckstore.GuidanceReceipt{}, &receipt, &summary); err == nil {
		t.Fatal("real time-sort overlap accepted")
	}
}
