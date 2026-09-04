package cninfo

import (
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

func TestClassifyPeriodicTitle(t *testing.T) {
	tests := []struct {
		title       string
		wantType    domain.FilingType
		wantVariant domain.FilingVariant
		wantPeriod  string
		eligible    bool
	}{
		{"平安银行股份有限公司2025年年度报告", domain.FilingTypeAnnual, domain.FilingVariantFull, "2025-12-31", true},
		{"平安银行股份有限公司2025年年度报告摘要", domain.FilingTypeAnnual, domain.FilingVariantSummary, "2025-12-31", false},
		{"关于2025年年度报告的更正公告", domain.FilingTypeAnnual, domain.FilingVariantCorrectionNotice, "2025-12-31", true},
		{"2026年第一季度报告（更正后）", domain.FilingTypeQ1, domain.FilingVariantCorrectedReport, "2026-03-31", true},
		{"关于延期披露2025年年度报告的公告", domain.FilingTypeUnknown, domain.FilingVariantOther, "", false},
		{"关于召开2025年年度报告说明会的公告", domain.FilingTypeUnknown, domain.FilingVariantOther, "", false},
		{"2025年度业绩快报", domain.FilingTypeUnknown, domain.FilingVariantOther, "", false},
	}
	for _, tc := range tests {
		t.Run(tc.title, func(t *testing.T) {
			filingType, variant, period, correction := ClassifyPeriodicTitle(tc.title)
			if filingType != tc.wantType || variant != tc.wantVariant {
				t.Fatalf("type/variant=%s/%s, want %s/%s", filingType, variant, tc.wantType, tc.wantVariant)
			}
			gotPeriod := ""
			if period != nil {
				gotPeriod = period.Format("2006-01-02")
			}
			if gotPeriod != tc.wantPeriod {
				t.Fatalf("period=%q, want %q", gotPeriod, tc.wantPeriod)
			}
			observation := domain.FilingObservation{FilingType: filingType, FilingVariant: variant, ReportPeriod: period, AnnouncementTime: time.Now()}
			if observation.EligiblePITAnchor() != tc.eligible {
				t.Fatalf("eligible=%v, want %v correction=%v", observation.EligiblePITAnchor(), tc.eligible, correction)
			}
		})
	}
}

func TestAnnouncementAvailabilityUsesNextChinaDayBoundary(t *testing.T) {
	providerLocalMidnight := time.Date(2026, 3, 28, 0, 0, 0, 0, chinaDisclosureLocation)
	date, available := announcementAvailability(providerLocalMidnight.UnixMilli())
	if date.Format("2006-01-02") != "2026-03-28" {
		t.Fatalf("announcement date=%s", date)
	}
	wantAvailable := time.Date(2026, 3, 29, 0, 0, 0, 0, chinaDisclosureLocation).UTC()
	if !available.Equal(wantAvailable) {
		t.Fatalf("available=%s, want %s", available, wantAvailable)
	}
}

func TestClientCatalogueAndDocument(t *testing.T) {
	providerTimestamp := time.Date(2026, 3, 28, 0, 0, 0, 0, chinaDisclosureLocation)
	wantAvailable := time.Date(2026, 3, 29, 0, 0, 0, 0, chinaDisclosureLocation).UTC()
	var server *httptest.Server
	server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case cataloguePath:
			if r.Method != http.MethodPost {
				t.Errorf("method=%s", r.Method)
			}
			body, _ := io.ReadAll(r.Body)
			form, _ := url.ParseQuery(string(body))
			if form.Get("category") != PeriodicReportCategories || form.Get("seDate") != "2026-03-01~2026-03-31" || form.Get("pageNum") != "1" {
				t.Errorf("form=%v", form)
			}
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprintf(w, `{
				"pageNum":1,"pageSize":30,"totalpages":1,"totalRecordNum":2,"hasMore":false,
				"announcements":[
					{"announcementId":"1212345678","secCode":"000001","secName":"平安银行","orgId":"gssz0000001","announcementTitle":"平安银行股份有限公司<em>2025年年度报告</em>","announcementTime":%d,"adjunctUrl":"finalpage/2026-03-28/report.PDF","adjunctType":"PDF","announcementType":"年度报告","columnId":"szse","pageColumn":"sz"},
					{"announcementId":"bad-time","secCode":"000002","secName":"万科A","announcementTitle":"2025年年度报告","announcementTime":0}
				]
			}`, providerTimestamp.UnixMilli())
		case "/finalpage/2026-03-28/report.PDF":
			w.Header().Set("Content-Type", "application/pdf; charset=binary")
			_, _ = w.Write([]byte("%PDF-test"))
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()

	client, err := NewClient(server.Client(), ClientOptions{
		BaseURL: server.URL, DocumentBaseURL: server.URL + "/", Retries: 0,
	})
	if err != nil {
		t.Fatal(err)
	}
	page, raw, err := client.CataloguePage(t.Context(), CatalogueRequest{
		Page: 1, PageSize: 30,
		StartDate: time.Date(2026, 3, 1, 0, 0, 0, 0, time.UTC),
		EndDate: time.Date(2026, 3, 31, 0, 0, 0, 0, time.UTC),
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(raw) == 0 || page.TotalPages != 1 || page.TotalRecords != 2 || len(page.Filings) != 1 || len(page.Issues) != 1 {
		t.Fatalf("page=%#v raw=%d", page, len(raw))
	}
	filing := page.Filings[0]
	if filing.SourceFilingID != "1212345678" || filing.ExchangeMIC != "XSHE" || filing.FilingType != domain.FilingTypeAnnual || filing.ReportPeriod == nil || filing.ReportPeriod.Format("2006-01-02") != "2025-12-31" {
		t.Fatalf("filing=%#v", filing)
	}
	if filing.AnnouncementDate.Format("2006-01-02") != "2026-03-28" || !filing.AnnouncementTime.Equal(wantAvailable) || filing.AnnouncementTimePrecision != domain.AnnouncementPrecisionDate || !strings.Contains(filing.Title, "2025年年度报告") {
		t.Fatalf("date/time/precision/title=%s/%s/%s/%q", filing.AnnouncementDate, filing.AnnouncementTime, filing.AnnouncementTimePrecision, filing.Title)
	}
	content, sourceURL, mediaType, err := client.FilingDocument(t.Context(), filing.DocumentLocator)
	if err != nil {
		t.Fatal(err)
	}
	if string(content) != "%PDF-test" || sourceURL != server.URL+"/finalpage/2026-03-28/report.PDF" || mediaType != "application/pdf" {
		t.Fatalf("document=%q url=%q type=%q", content, sourceURL, mediaType)
	}
}

func TestClientRejectsDocumentHostEscape(t *testing.T) {
	client, err := NewClient(http.DefaultClient, ClientOptions{
		BaseURL: "https://www.cninfo.com.cn", DocumentBaseURL: "https://static.cninfo.com.cn/", Retries: 0,
	})
	if err != nil {
		t.Fatal(err)
	}
	if _, _, _, err := client.FilingDocument(t.Context(), "https://example.com/report.pdf"); err == nil {
		t.Fatal("expected document host guard")
	}
}
