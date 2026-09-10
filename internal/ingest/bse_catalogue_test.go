package ingest

import (
	"crypto/sha256"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/source/cninfo"
)

func TestBSECatalogueUsesAnnouncementDateCodes(t *testing.T) {
	root := filepath.Join("testdata", "bse-code-transition-2025")
	f, err := os.Open(filepath.Join(root, "transitions.csv"))
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	rows, err := csv.NewReader(f).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	pairs := map[string][]string{}
	for _, row := range rows[1:] {
		pairs[row[1]] = row
	}
	for _, code := range []string{"920819", "920123"} {
		t.Run(code, func(t *testing.T) {
			// Source keys are explicit; retain the exact HTTP response without rewriting totalpages=0.
			var source struct {
				URL     string                  `json:"url"`
				Method  string                  `json:"method"`
				SHA     string                  `json:"sha256"`
				Request cninfo.CatalogueRequest `json:"request"`
			}
			b, err := os.ReadFile(filepath.Join(root, code+"-catalogue.source.json"))
			if err != nil {
				t.Fatal(err)
			}
			if err = json.Unmarshal(b, &source); err != nil {
				t.Fatal(err)
			}
			raw, err := os.ReadFile(filepath.Join(root, code+"-catalogue.json"))
			if err != nil {
				t.Fatal(err)
			}
			if source.SHA != fmt.Sprintf("%x", sha256.Sum256(raw)) || source.URL != "https://www.cninfo.com.cn/new/hisAnnouncement/query" || source.Method != "POST" {
				t.Fatal("catalogue evidence mismatch")
			}
			calls := 0
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				if err := r.ParseForm(); err != nil {
					t.Error(err)
				}
				if r.Form.Get("stock") != code+","+source.Request.OrganizationID {
					t.Error("wrong company request")
				}
				w.Write(raw)
			}))
			defer server.Close()
			client, err := cninfo.NewClient(server.Client(), cninfo.ClientOptions{BaseURL: server.URL, DocumentBaseURL: server.URL + "/"})
			if err != nil {
				t.Fatal(err)
			}
			page, _, err := client.CataloguePage(t.Context(), source.Request)
			if err != nil {
				t.Fatal(err)
			}
			want := 16
			if code == "920123" {
				want = 11
			}
			if calls != 1 || len(page.Filings) != want || page.TotalRecords != want || page.HasMore || len(page.Issues) != 0 {
				t.Fatal("incomplete source response", page)
			}
			pair := pairs[code]
			cutover, err := time.Parse("2006-01-02", pair[4])
			if err != nil {
				t.Fatal(err)
			}
			oldCount, newCount, lateCorrection := 0, 0, 0
			for _, filing := range page.Filings {
				expected := code
				if filing.AnnouncementDate.Before(cutover) {
					expected = pair[0]
					oldCount++
				} else {
					newCount++
				}
				if filing.ProviderCode != expected || filing.ProviderOrgID != source.Request.OrganizationID || filing.ExchangeMIC != "XBSE" {
					t.Fatal("code/date/org/exchange evidence mismatch", filing)
				}
				if filing.ReportPeriod != nil && filing.ReportPeriod.Year() == 2024 && filing.AnnouncementDate.Year() == 2026 && strings.Contains(filing.Title, "更正") {
					lateCorrection++
					if filing.ProviderCode != code {
						t.Fatal("report period incorrectly used as code date")
					}
				}
			}
			if code == "920819" && (oldCount != 3 || newCount != 13 || lateCorrection != 3) {
				t.Fatal(oldCount, newCount, lateCorrection)
			}
			if code == "920123" && (oldCount != 5 || newCount != 6) {
				t.Fatal(oldCount, newCount)
			}
		})
	}
}
