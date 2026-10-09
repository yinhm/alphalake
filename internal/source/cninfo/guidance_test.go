package cninfo

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
	"time"
)

func TestGuidanceRealCatalogueAndClassification(t *testing.T) {
	seen := map[string]bool{}
	total := 0
	for _, path := range []string{"testdata/guidance/page1.json", "testdata/guidance/page2.json"} {
		raw, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		page, err := ParseCataloguePage(raw)
		if err != nil {
			t.Fatal(err)
		}
		if page.TotalRecords != 16 || page.TotalPages != 1 {
			t.Fatal(page.TotalRecords, page.TotalPages)
		}
		for _, filing := range page.Filings {
			if seen[filing.SourceFilingID] {
				t.Fatal("overlapping source pages")
			}
			seen[filing.SourceFilingID] = true
			total++
			if page.RowPositions[filing.SourceFilingID] < 1 || filing.EligiblePITAnchor() {
				t.Fatal(filing)
			}
		}
	}
	if total != 16 {
		t.Fatal(total)
	}
	for _, test := range []struct{ title, kind, end, basis string }{
		{"2026年前三季度业绩预告", "forecast", "2026-09-30", "ytd"},
		{"广汇能源股份有限公司2026年前三季度业绩预增公告", "forecast", "2026-09-30", "ytd"},
		{"关于2026年前三季度业绩预告监管工作函的回复公告", "other", "", "unknown"},
		{"2026年第三季度业绩预告修正公告", "forecast", "2026-09-30", "quarter"},
		{"2026年前三季度业绩快报公告", "preliminary", "2026-09-30", "ytd"},
		{"关于召开2026年半年度业绩说明会的预告公告", "other", "", "unknown"},
	} {
		kind, _, end, basis := GuidanceTitle(test.title)
		if kind != test.kind || end != test.end || basis != test.basis {
			t.Fatal(test, kind, end, basis)
		}
	}
}

func TestGuidanceCatalogueUsesVerifiedCategory(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		r.ParseForm()
		if r.Form.Get("category") != GuidanceCategories || r.Form.Get("sortName") != "" || r.Form.Get("sortType") != "" {
			t.Error(r.Form)
		}
		w.Write([]byte(`{"announcements":[],"totalAnnouncement":0}`))
	}))
	defer server.Close()
	client, err := NewClient(server.Client(), ClientOptions{BaseURL: server.URL})
	if err != nil {
		t.Fatal(err)
	}
	_, _, err = client.CataloguePage(context.Background(), CatalogueRequest{Kind: CatalogueGuidance, Page: 1, PageSize: 100, StartDate: time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC), EndDate: time.Date(2026, 10, 9, 0, 0, 0, 0, time.UTC)})
	if err != nil {
		t.Fatal(err)
	}
}
