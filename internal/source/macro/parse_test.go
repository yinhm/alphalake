package macro

import (
	"bytes"
	"compress/gzip"
	"encoding/json"
	"io"
	"os"
	"strings"
	"testing"
)

type fixture struct{ Dataset, URL, SHA256 string }

func fixtures(t *testing.T) []fixture {
	t.Helper()
	raw, err := os.ReadFile("testdata/sources.json")
	if err != nil {
		t.Fatal(err)
	}
	var rows []fixture
	if err = json.Unmarshal(raw, &rows); err != nil {
		t.Fatal(err)
	}
	return rows
}
func fixtureRaw(t *testing.T, key string) []byte {
	t.Helper()
	raw, err := os.ReadFile("testdata/" + key + ".html.gz")
	if err != nil {
		t.Fatal(err)
	}
	reader, err := gzip.NewReader(bytes.NewReader(raw))
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	raw, err = io.ReadAll(io.LimitReader(reader, (8<<20)+1))
	if err != nil {
		t.Fatal(err)
	}
	return raw
}
func TestOfficialMacroPublicationFixtures(t *testing.T) {
	all := map[string]Snapshot{}
	for _, entry := range fixtures(t) {
		t.Run(entry.Dataset, func(t *testing.T) {
			feed, err := FindFeed(entry.Dataset)
			if err != nil {
				t.Fatal(err)
			}
			snapshot, err := Parse(fixtureRaw(t, entry.Dataset), feed, entry.URL)
			if err != nil {
				t.Fatal(err)
			}
			if snapshot.SHA256 != entry.SHA256 {
				t.Fatal("source bytes changed")
			}
			all[entry.Dataset] = snapshot
			t.Log("series", len(snapshot.Series), "observations", len(snapshot.Observations), "future placeholders", snapshot.FuturePlaceholders)
		})
	}
	if len(all) != len(Feeds()) {
		t.Fatalf("parsed %d / %d official feeds", len(all), len(Feeds()))
	}
	cases := []struct{ feed, code, day, value string }{
		{"gdp", "cn_gdp_nominal", "2026-09-30", "37536700000000.000000000000"},
		{"gdp", "cn_gdp_real_yoy", "2026-09-30", "0.045000000000"},
		{"gdp", "cn_gdp_real_qoq_sa", "2021-03-31", "0.000000000000"},
		{"cpi", "cn_cpi_yoy", "2026-09-30", "0.009000000000"},
		{"ppi", "cn_ppi_yoy", "2026-09-30", "0.050000000000"},
		{"pmi", "cn_pmi_manufacturing", "2025-09-30", "49.800000000000"},
		{"investment", "cn_fixed_asset_investment_ytd_yoy", "2026-09-30", "-0.072000000000"},
		{"retail", "cn_retail_sales", "2026-09-30", "4243300000000.000000000000"},
		{"property", "cn_property_unsold_area", "2026-09-30", "748250000.000000000000"},
		{"capacity", "cn_capacity_industrial", "2026-09-30", "0.731000000000"},
		{"household", "cn_household_disposable_income", "2026-09-30", "34230.000000000000"},
		{"household", "cn_household_disposable_income_real_yoy", "2026-09-30", "0.044000000000"},
		{"economy", "cn_urban_surveyed_unemployment", "2026-09-30", "0.052000000000"},
		{"economy", "cn_goods_exports_ytd", "2026-09-30", "23197900000000.000000000000"},
		{"profits", "cn_industrial_profit_ytd", "2026-08-31", "5271980000000.000000000000"},
		{"money", "cn_money_m1_2025_stock", "2026-09-30", "116756384000000.000000000000"},
		{"afre_flow", "cn_afre_rmb_loans_flow", "2026-04-30", "-400600000000.000000000000"},
		{"afre_stock", "cn_afre_stock", "2026-09-30", "468170000000000.000000000000"},
		{"fiscal", "cn_fiscal_general_revenue_ytd", "2026-08-31", "15663300000000.000000000000"},
		{"fiscal", "cn_fiscal_fund_revenue_ytd_yoy", "2026-08-31", "-0.190000000000"},
	}
	for _, test := range cases {
		found := false
		for _, o := range all[test.feed].Observations {
			if o.SeriesCode == test.code && o.PeriodEnd == test.day {
				found = true
				if o.Value == nil || *o.Value != test.value {
					t.Errorf("%s %s got %v, want %s", test.code, test.day, o.Value, test.value)
				}
			}
		}
		if !found {
			t.Errorf("missing %s / %s", test.code, test.day)
		}
	}
}

func TestMacroUnitsDuplicatedViewsAndBadNumbersAreRejected(t *testing.T) {
	var gdp fixture
	for _, f := range fixtures(t) {
		if f.Dataset == "gdp" {
			gdp = f
		}
	}
	feed, _ := FindFeed("gdp")
	original := fixtureRaw(t, "gdp")
	for _, raw := range [][]byte{
		bytes.ReplaceAll(original, []byte("绝对额（亿元）"), []byte("绝对额（万元）")),
		bytes.Replace(original, []byte("375367"), []byte("375368"), 1),
		bytes.ReplaceAll(original, []byte("375367"), []byte("NaN")),
		[]byte("<html>access denied</html>"),
	} {
		if _, err := Parse(raw, feed, gdp.URL); err == nil {
			t.Fatal("unsupported/tampered official source accepted")
		}
	}
	s, err := Parse(original, feed, gdp.URL)
	if err != nil {
		t.Fatal(err)
	}
	for i := range s.Observations {
		if s.Observations[i].Value != nil {
			s.Observations[i].RawUnit = "CNY_trillion"
			break
		}
	}
	if err := Validate(s); err == nil {
		t.Fatal("unit reinterpretation accepted")
	}
	if _, err := Parse(original, feed, "https://example.invalid/data"); err == nil {
		t.Fatal("unreviewed source accepted")
	}
}

func TestPBCSourceDefinitionAndFuturePlaceholders(t *testing.T) {
	for _, entry := range fixtures(t) {
		if entry.Dataset != "money" {
			continue
		}
		feed, _ := FindFeed(entry.Dataset)
		raw := fixtureRaw(t, entry.Dataset)
		snapshot, err := Parse(raw, feed, entry.URL)
		if err != nil {
			t.Fatal(err)
		}
		if snapshot.FuturePlaceholders != 9 || len(snapshot.Observations) != 27 || snapshot.PublishedAt != nil {
			t.Fatal("future placeholders became zero or unknown publication time was invented", snapshot.FuturePlaceholders, len(snapshot.Observations))
		}
		for _, o := range snapshot.Observations {
			if o.PeriodEnd > "2026-09-30" || o.PeriodStart != o.PeriodEnd {
				t.Fatal("stock treated as monthly flow")
			}
		}
		if _, err := Parse(bytes.ReplaceAll(raw, []byte("2025"), []byte("2024")), feed, entry.URL); err == nil {
			t.Fatal("old M1 scope accepted")
		}
	}
	for _, unit := range []string{"percent", "CNY_trillion", "diffusion_index_percent_scale"} {
		value, _, err := normalize("0", unit)
		if err != nil || value == nil || *value != "0.000000000000" {
			t.Fatal("reported zero lost", unit)
		}
	}
	if value, _, err := normalize("—", "percent"); err != nil || value != nil {
		t.Fatal("missing was converted to zero")
	}
	if start, end := period(2026, 2, "month_or_jan_feb"); start != "2026-01-01" || end != "2026-02-28" {
		t.Fatal(start, end)
	}
	if start, _ := period(2026, 2, "month"); !strings.HasPrefix(start, "2026-02") {
		t.Fatal("ordinary monthly series combined with January")
	}
}
