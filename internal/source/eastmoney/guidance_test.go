package eastmoney

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"strings"
	"testing"
)

func TestGuidanceRealAmountsAndPeriods(t *testing.T) {
	raw, err := os.ReadFile("testdata/dinglong.json")
	if err != nil {
		t.Fatal(err)
	}
	page, err := ParsePage(raw)
	if err != nil {
		t.Fatal(err)
	}
	gs, issues, err := Normalize(page.Rows[:2], "2026-09-30")
	if err != nil || len(gs) != 1 || len(issues) != 0 {
		t.Fatal(gs, issues, err)
	}
	g := gs[0]
	if g.Ticker != "SZSE:300054" || g.PeriodStart != "2026-01-01" || g.PeriodBasis != "ytd" {
		t.Fatal(g)
	}
	for name, amounts := range map[string][2]string{"forecast_parent_net_income": {"840000000", "860000000"}, "forecast_adjusted_net_income": {"780000000", "800000000"}} {
		m := g.Metrics[name]
		if *m.Lower != amounts[0] || *m.Upper != amounts[1] || m.Unit != "CNY" || m.Status != "valid" {
			t.Fatal(name, m)
		}
	}
	// Prediction type belongs to a metric, not to the announcement identity.
	originalType := page.Rows[0].Type
	page.Rows[0].Type = "预亏"
	mixed, _, err := Normalize(page.Rows[:2], "2026-09-30")
	if err != nil || len(mixed) != 1 || mixed[0].PredictType != "mixed_metric_types" {
		t.Fatal("metric type split one disclosure", err)
	}
	page.Rows[0].Type = originalType
	// Reordering source rows must not create new observation versions.
	page.Rows[0], page.Rows[1] = page.Rows[1], page.Rows[0]
	reordered, _, err := Normalize(page.Rows[:2], "2026-09-30")
	if err != nil || reordered[0].ID != g.ID {
		t.Fatal(reordered, err)
	}
	if _, _, err = Normalize(page.Rows, "2026-09-30"); err == nil {
		t.Fatal("mixed requested periods accepted")
	}
	q3, _ := os.ReadFile("testdata/q3.json")
	epsPage, err := ParsePage(q3)
	if err != nil {
		t.Fatal(err)
	}
	eps, _, err := Normalize(epsPage.Rows[:1], "2026-09-30")
	if err != nil {
		t.Fatal(err)
	}
	m := eps[0].Metrics["forecast_eps"]
	if *m.Lower != "0.2652" || m.Unit != "CNY/share" {
		t.Fatal(m)
	}
	revenueRaw, _ := os.ReadFile("testdata/revenue-page.json")
	revenuePage, e := ParsePage(revenueRaw)
	if e != nil {
		t.Fatal(e)
	}
	matched := false
	for _, row := range revenuePage.Rows {
		if row.Code == "301689" && row.MetricCode == "006" {
			values, _, e := Normalize([]Row{row}, "2026-09-30")
			if e != nil {
				t.Fatal(e)
			}
			m := values[0].Metrics["forecast_revenue"]
			if *m.Lower != "1520000000" || *m.Upper != "1680000000" || m.Unit != "CNY" || m.Scope != "unspecified" {
				t.Fatal(m)
			}
			matched = true
		}
	}
	if !matched {
		t.Fatal("real revenue source sample missing")
	}
	bad, _ := os.ReadFile("testdata/business-error.json")
	if _, err = ParsePage(bad); err == nil {
		t.Fatal("HTTP200 business error accepted")
	}
	if _, err = ParsePage([]byte(`{"success":true,"result":null}`)); err == nil {
		t.Fatal("missing result accepted")
	}
}

func TestGuidanceDecimalAndAmbiguities(t *testing.T) {
	for input, want := range map[string]string{"0": "0", "-2.5": "-2.5", `"840000000"`: "840000000", "1.2e3": "1200", "0.0000000001": "0.0000000001"} {
		value, err := Decimal(json.RawMessage(input))
		if err != nil || value == nil || *value != want {
			t.Fatal(input, value, err)
		}
	}
	for _, input := range []string{`"NaN"`, `1e100000`, `0.00000000001`, `10000000000000000000000000000`} {
		if _, err := Decimal(json.RawMessage(input)); err == nil {
			t.Fatal("precision accepted", input)
		}
	}
	raw, _ := os.ReadFile("testdata/dinglong.json")
	p, _ := ParsePage(raw)
	rows := p.Rows[:2]
	if rows[0].MetricCode != "004" {
		rows[0], rows[1] = rows[1], rows[0]
	}
	rows[0].Lower = json.RawMessage(`900000000`)
	gs, issues, err := Normalize(rows, "2026-09-30")
	if err != nil || len(issues) == 0 || gs[0].Metrics["forecast_parent_net_income"].Status != "unresolved" || gs[0].Metrics["forecast_adjusted_net_income"].Status != "valid" {
		t.Fatal(gs, issues, err)
	}
	rows[0].Lower = json.RawMessage(`0`)
	rows[0].Upper = json.RawMessage(`0`)
	gs, _, err = Normalize(rows, "2026-09-30")
	if err != nil || *gs[0].Metrics["forecast_parent_net_income"].Lower != "0" {
		t.Fatal(gs, err)
	}
	rows[0].Lower = json.RawMessage(`-20`)
	rows[0].Upper = json.RawMessage(`-10`)
	gs, _, err = Normalize(rows, "2026-09-30")
	if err != nil || gs[0].Metrics["forecast_parent_net_income"].Status != "valid" {
		t.Fatal(gs, err)
	}
	duplicate := rows[0]
	duplicate.Lower = json.RawMessage(`-30`)
	gs, _, err = Normalize(append(rows, duplicate), "2026-09-30")
	if err != nil || gs[0].Metrics["forecast_parent_net_income"].Status != "conflicting" {
		t.Fatal(gs, err)
	}
	rows[0].Content = "预计盈利"
	gs, _, err = Normalize(rows[:1], "2026-09-30")
	if err != nil || gs[0].PeriodBasis != "unknown" || gs[0].PeriodStart != "" {
		t.Fatal(gs, err)
	}
}

func TestGuidanceHTTPContract(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		if q.Get("reportName") != GuidanceReport || q.Get("filter") != "(REPORT_DATE='2026-09-30')" || q.Get("sortColumns") != "NOTICE_DATE,SECUCODE,PREDICT_FINANCE_CODE" {
			t.Error(q)
		}
		w.Write([]byte(`{"success":true,"result":{"count":0,"pages":0,"data":[]}}`))
	}))
	defer server.Close()
	client := &Client{HTTP: server.Client(), Endpoint: server.URL}
	raw, locator, err := client.GuidancePage(context.Background(), "2026-09-30", 1, 100)
	if err != nil || !strings.HasPrefix(locator, server.URL) {
		t.Fatal(locator, err)
	}
	p, err := ParsePage(raw)
	if err != nil || !reflect.DeepEqual(p.Rows, []Row{}) {
		t.Fatal(p, err)
	}
	if _, _, err = client.GuidancePage(context.Background(), "invalid'", 1, 100); err == nil {
		t.Fatal("invalid date accepted")
	}
}
