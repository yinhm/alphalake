package domain

import (
	"fmt"
	"regexp"
	"strings"
	"time"
)

var monthPeriod = regexp.MustCompile(`((?:19|20)[0-9]{2})年\s*([0-9]{1,2})\s*[-—至]\s*([0-9]{1,2})月`)
var quarterPeriod = regexp.MustCompile(`((?:19|20)[0-9]{2})年\s*第?([一二三四1234])季度`)

// GuidanceOperatingPeriod requires explicit operating dates, not just REPORT_DATE.
func GuidanceOperatingPeriod(content, report string) (string, string) {
	if len(report) < 10 {
		return "", "unknown"
	}
	end, err := time.Parse("2006-01-02", report[:10])
	if err != nil {
		return "", "unknown"
	}
	year, first, last := end.Year(), 0, 0
	if match := monthPeriod.FindStringSubmatch(content); match != nil {
		fmt.Sscan(match[1], &year)
		fmt.Sscan(match[2], &first)
		fmt.Sscan(match[3], &last)
	} else if match := quarterPeriod.FindStringSubmatch(content); match != nil {
		fmt.Sscan(match[1], &year)
		q := strings.Index("一二三四", match[2])/3 + 1
		if match[2] >= "1" && match[2] <= "4" {
			fmt.Sscan(match[2], &q)
		}
		// 第一季度没有累计/单季歧义；其余季度标签可能指累计预告。
		if q != 1 {
			return "", "unknown"
		}
		first, last = (q-1)*3+1, q*3
	} else if strings.Contains(content, fmt.Sprintf("%d年", year)) && (strings.Contains(content, "全年") || strings.Contains(content, "年度")) {
		first, last = 1, 12
	}
	if first < 1 || first > last || last > 12 || year != end.Year() || int(end.Month()) != last || end.Day() != time.Date(year, time.Month(last)+1, 0, 0, 0, 0, 0, time.UTC).Day() {
		return "", "unknown"
	}
	basis := "quarter"
	if first == 1 {
		basis = "ytd"
	}
	if first == 1 && last == 12 {
		basis = "fiscal_year"
	}
	if basis == "quarter" && (last-first != 2 || (first-1)%3 != 0) {
		return "", "unknown"
	}
	return time.Date(year, time.Month(first), 1, 0, 0, 0, 0, time.UTC).Format("2006-01-02"), basis
}
