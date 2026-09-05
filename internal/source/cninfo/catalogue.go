package cninfo

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"html"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

const (
	Source                   = "cninfo"
	CatalogueParserVersion   = "cninfo-catalogue-v1"
	FilingClassifierVersion  = "cninfo-periodic-title-v1"
	PeriodicReportCategories = "category_ndbg_szsh;category_bndbg_szsh;category_yjdbg_szsh;category_sjdbg_szsh;" +
		"category_ndbg_bj;category_bndbg_bj;category_yjdbg_bj;category_sjdbg_bj"
)

type CatalogueRequest struct {
	Page      int
	PageSize  int
	StartDate time.Time
	EndDate   time.Time
}

type CatalogueIssue struct {
	SourceFilingID string
	ProviderCode   string
	Reason         string
}

type CataloguePage struct {
	Page         int
	PageSize     int
	TotalPages   int
	TotalRecords int
	HasMore      bool
	Filings      []domain.FilingObservation
	Issues       []CatalogueIssue
}

type rawCatalogueResponse struct {
	Announcements     []rawAnnouncement `json:"announcements"`
	PageNum           int               `json:"pageNum"`
	PageSize          int               `json:"pageSize"`
	TotalPages        int               `json:"totalpages"`
	TotalPagesAlt     int               `json:"totalPages"`
	TotalRecordNum    int               `json:"totalRecordNum"`
	TotalAnnouncement int               `json:"totalAnnouncement"`
	HasMore           bool              `json:"hasMore"`
}

type rawAnnouncement struct {
	ID                flexibleString `json:"id"`
	AnnouncementID    flexibleString `json:"announcementId"`
	SecCode           string         `json:"secCode"`
	SecName           string         `json:"secName"`
	OrgID             string         `json:"orgId"`
	AnnouncementTitle string         `json:"announcementTitle"`
	AnnouncementTime  flexibleInt64  `json:"announcementTime"`
	AdjunctURL        string         `json:"adjunctUrl"`
	AdjunctType       string         `json:"adjunctType"`
	AnnouncementType  string         `json:"announcementType"`
	ColumnID          string         `json:"columnId"`
	PageColumn        string         `json:"pageColumn"`
}

type flexibleString string

func (v *flexibleString) UnmarshalJSON(data []byte) error {
	data = bytes.TrimSpace(data)
	if bytes.Equal(data, []byte("null")) || len(data) == 0 {
		*v = ""
		return nil
	}
	var s string
	if len(data) > 0 && data[0] == '"' {
		if err := json.Unmarshal(data, &s); err != nil {
			return err
		}
		*v = flexibleString(s)
		return nil
	}
	var n json.Number
	if err := json.Unmarshal(data, &n); err != nil {
		return err
	}
	*v = flexibleString(n.String())
	return nil
}

type flexibleInt64 int64

func (v *flexibleInt64) UnmarshalJSON(data []byte) error {
	data = bytes.TrimSpace(data)
	if bytes.Equal(data, []byte("null")) || len(data) == 0 {
		*v = 0
		return nil
	}
	var raw string
	if len(data) > 0 && data[0] == '"' {
		if err := json.Unmarshal(data, &raw); err != nil {
			return err
		}
	} else {
		raw = string(data)
	}
	n, err := strconv.ParseInt(strings.TrimSpace(raw), 10, 64)
	if err != nil {
		return err
	}
	*v = flexibleInt64(n)
	return nil
}

func ParseCataloguePage(raw []byte) (CataloguePage, error) {
	var response rawCatalogueResponse
	if err := json.Unmarshal(raw, &response); err != nil {
		return CataloguePage{}, fmt.Errorf("decode CNINFO announcement catalogue: %w", err)
	}
	page := CataloguePage{
		Page: response.PageNum, PageSize: response.PageSize,
		TotalPages: response.TotalPages, TotalRecords: response.TotalRecordNum,
		HasMore: response.HasMore,
	}
	if page.TotalPages == 0 {
		page.TotalPages = response.TotalPagesAlt
	}
	if page.TotalRecords == 0 {
		page.TotalRecords = response.TotalAnnouncement
	}
	if page.TotalPages == 0 && page.TotalRecords > 0 && page.PageSize > 0 {
		page.TotalPages = (page.TotalRecords + page.PageSize - 1) / page.PageSize
	}
	page.Filings = make([]domain.FilingObservation, 0, len(response.Announcements))
	for _, item := range response.Announcements {
		filing, issue := normalizeAnnouncement(item)
		if issue != nil {
			page.Issues = append(page.Issues, *issue)
			continue
		}
		page.Filings = append(page.Filings, filing)
	}
	return page, nil
}

func normalizeAnnouncement(item rawAnnouncement) (domain.FilingObservation, *CatalogueIssue) {
	filingID := strings.TrimSpace(string(item.AnnouncementID))
	if filingID == "" {
		filingID = strings.TrimSpace(string(item.ID))
	}
	code := strings.TrimSpace(item.SecCode)
	title := cleanTitle(item.AnnouncementTitle)
	issue := func(reason string) (domain.FilingObservation, *CatalogueIssue) {
		return domain.FilingObservation{}, &CatalogueIssue{SourceFilingID: filingID, ProviderCode: code, Reason: reason}
	}
	if filingID == "" {
		return issue("announcement has no provider filing ID")
	}
	if code == "" {
		return issue("announcement has no security code")
	}
	if title == "" {
		return issue("announcement has no title")
	}
	millis := int64(item.AnnouncementTime)
	if millis <= 0 {
		return issue("announcement has no positive provider timestamp")
	}
	announcementDate, availableAt := announcementAvailability(millis)
	if announcementDate.Year() < 1990 || announcementDate.Year() > 2200 {
		return issue(fmt.Sprintf("announcement timestamp resolves to implausible year %d", announcementDate.Year()))
	}
	filingType, variant, period, isCorrection := ClassifyPeriodicTitle(title)
	return domain.FilingObservation{
		Source:                    Source,
		SourceFilingID:            filingID,
		ProviderCode:              code,
		ExchangeMIC:               exchangeMIC(item.ColumnID, item.PageColumn),
		SecurityName:              strings.TrimSpace(item.SecName),
		Title:                     title,
		FilingType:                filingType,
		FilingVariant:             variant,
		ReportPeriod:              period,
		AnnouncementDate:          announcementDate,
		AnnouncementTime:          availableAt,
		AnnouncementTimePrecision: domain.AnnouncementPrecisionDate,
		DocumentLocator:           strings.TrimSpace(item.AdjunctURL),
		RawCategory:               strings.TrimSpace(item.AnnouncementType),
		ClassifierVersion:         FilingClassifierVersion,
		IsCorrection:              isCorrection,
		ProviderOrgID:             strings.TrimSpace(item.OrgID),
		ProviderColumnID:          strings.TrimSpace(item.ColumnID),
		ProviderPageColumn:        strings.TrimSpace(item.PageColumn),
		RawAnnouncementTimeMillis: millis,
	}, nil
}

var yearPattern = regexp.MustCompile(`(?:19|20)\d{2}`)

// ClassifyPeriodicTitle derives report-period semantics only from explicit
// periodic-report wording. It does not classify forecasts, earnings flashes,
// postponement notices, inquiry letters, or investor-relations announcements as
// financial statements merely because their titles contain words such as 年报.
func ClassifyPeriodicTitle(title string) (domain.FilingType, domain.FilingVariant, *time.Time, bool) {
	title = cleanTitle(title)
	yearText := yearPattern.FindString(title)
	if yearText == "" {
		return domain.FilingTypeUnknown, domain.FilingVariantOther, nil, false
	}
	year, err := strconv.Atoi(yearText)
	if err != nil || year < 1990 || year > 2200 {
		return domain.FilingTypeUnknown, domain.FilingVariantOther, nil, false
	}

	// Category search can return documents that merely discuss a report. These
	// references are evidence but not statement filings and must not become PIT
	// announcement anchors. Explicit report correction/revision wording is handled
	// below and therefore intentionally absent from this exclusion set.
	if containsAny(title,
		"延期披露", "推迟披露", "预约披露", "披露日期", "披露时间",
		"问询函", "监管工作函", "回复公告", "说明会", "业绩说明会",
		"董事会决议", "监事会决议", "审议", "编制工作", "年报工作",
		"业绩预告", "业绩快报",
	) {
		return domain.FilingTypeUnknown, domain.FilingVariantOther, nil, false
	}

	var filingType domain.FilingType
	var month time.Month
	var day int
	switch {
	case strings.Contains(title, "第一季度报告") || strings.Contains(title, "一季度报告"):
		filingType, month, day = domain.FilingTypeQ1, time.March, 31
	case strings.Contains(title, "半年度报告") || strings.Contains(title, "中期报告"):
		filingType, month, day = domain.FilingTypeH1, time.June, 30
	case strings.Contains(title, "第三季度报告") || strings.Contains(title, "三季度报告"):
		filingType, month, day = domain.FilingTypeQ3, time.September, 30
	case strings.Contains(title, "年度报告"):
		filingType, month, day = domain.FilingTypeAnnual, time.December, 31
	default:
		return domain.FilingTypeUnknown, domain.FilingVariantOther, nil, false
	}

	isCorrection := containsAny(title, "更正", "修正", "修订", "更新", "补充")
	variant := domain.FilingVariantFull
	switch {
	case strings.Contains(title, "摘要"):
		variant = domain.FilingVariantSummary
	case containsAny(title, "更正公告", "修正公告"):
		variant = domain.FilingVariantCorrectionNotice
	case containsAny(title, "更正后", "修正后"):
		variant = domain.FilingVariantCorrectedReport
	case containsAny(title, "修订版", "修订稿", "修订后", "更新后", "补充版"):
		variant = domain.FilingVariantRevision
	}
	period := time.Date(year, month, day, 0, 0, 0, 0, time.UTC)
	return filingType, variant, &period, isCorrection
}

func exchangeMIC(columnID, pageColumn string) string {
	switch strings.ToUpper(strings.TrimSpace(pageColumn)) {
	case "SHZB":
		return "XSHG"
	case "SZZB":
		return "XSHE"
	}
	value := strings.ToLower(strings.TrimSpace(columnID + " " + pageColumn))
	switch {
	case strings.Contains(value, "szse") || strings.Contains(value, "shen") || strings.Contains(value, "深"):
		return "XSHE"
	case strings.Contains(value, "bse") || strings.Contains(value, "bj") || strings.Contains(value, "北"):
		return "XBSE"
	case strings.Contains(value, "sse") || strings.Contains(value, "shanghai") || strings.Contains(value, "沪"):
		return "XSHG"
	default:
		return ""
	}
}

func cleanTitle(value string) string {
	value = html.UnescapeString(value)
	var out strings.Builder
	inTag := false
	for _, r := range value {
		switch r {
		case '<':
			inTag = true
		case '>':
			inTag = false
		default:
			if !inTag {
				out.WriteRune(r)
			}
		}
	}
	return strings.TrimSpace(strings.Join(strings.Fields(out.String()), " "))
}

func containsAny(value string, needles ...string) bool {
	for _, needle := range needles {
		if strings.Contains(value, needle) {
			return true
		}
	}
	return false
}

func ValidateCatalogueRequest(request CatalogueRequest) error {
	if request.Page <= 0 || request.PageSize <= 0 || request.PageSize > 100 {
		return errors.New("CNINFO page must be positive and page size must be in [1,100]")
	}
	if request.StartDate.IsZero() || request.EndDate.IsZero() {
		return errors.New("CNINFO catalogue start/end dates are required")
	}
	start := dateUTC(request.StartDate)
	end := dateUTC(request.EndDate)
	if end.Before(start) {
		return errors.New("CNINFO catalogue end date precedes start date")
	}
	return nil
}

func dateUTC(value time.Time) time.Time {
	y, m, d := value.Date()
	return time.Date(y, m, d, 0, 0, 0, 0, time.UTC)
}
