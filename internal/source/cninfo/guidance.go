package cninfo

import (
	"fmt"
	"strings"

	"github.com/yinhm/alphalake/internal/domain"
)

const GuidanceClassifierVersion = "cninfo-guidance-v2"

// GuidanceTitle classifies disclosure type separately from periodic PIT anchors.
func GuidanceTitle(title string) (kind, start, end, basis string) {
	title = cleanTitle(title)
	if containsAny(title, "说明会", "提示性公告", "摘要", "取消", "进展", "回复", "问询函", "监管工作函") {
		return "other", "", "", "unknown"
	}
	kind = "other"
	if containsAny(title, "业绩预告", "业绩预增", "业绩预减", "业绩预盈", "业绩预亏") {
		kind = "forecast"
	}
	if strings.Contains(title, "业绩快报") {
		kind = "preliminary"
	}
	if kind == "other" {
		return kind, "", "", "unknown"
	}
	yearText := yearPattern.FindString(title)
	var year int
	if yearText == "" {
		return kind, "", "", "unknown"
	}
	fmt.Sscan(yearText, &year)
	switch {
	case strings.Contains(title, "前三季度"):
		return kind, fmt.Sprintf("%d-01-01", year), fmt.Sprintf("%d-09-30", year), "ytd"
	case containsAny(title, "半年度", "上半年"):
		return kind, fmt.Sprintf("%d-01-01", year), fmt.Sprintf("%d-06-30", year), "ytd"
	case strings.Contains(title, "第一季度"):
		return kind, fmt.Sprintf("%d-01-01", year), fmt.Sprintf("%d-03-31", year), "ytd"
	case strings.Contains(title, "第三季度"):
		return kind, fmt.Sprintf("%d-07-01", year), fmt.Sprintf("%d-09-30", year), "quarter"
	case strings.Contains(title, "年度") || strings.Contains(title, "全年"):
		return kind, fmt.Sprintf("%d-01-01", year), fmt.Sprintf("%d-12-31", year), "fiscal_year"
	}
	// Explicit month ranges are accepted; an ambiguous “三季度” is not guessed.
	for _, date := range []string{fmt.Sprintf("%d-03-31", year), fmt.Sprintf("%d-06-30", year), fmt.Sprintf("%d-09-30", year), fmt.Sprintf("%d-12-31", year)} {
		start, basis = domain.GuidanceOperatingPeriod(title, date)
		if basis != "unknown" {
			return kind, start, date, basis
		}
	}
	return kind, "", "", "unknown"
}
