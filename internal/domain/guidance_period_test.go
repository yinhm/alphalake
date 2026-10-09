package domain

import "testing"

func TestGuidanceOperatingPeriod(t *testing.T) {
	for _, test := range []struct{ text, end, start, basis string }{
		{"预计2026年1-9月盈利", "2026-09-30", "2026-01-01", "ytd"},
		{"2026年第三季度", "2026-09-30", "2026-07-01", "quarter"},
		{"2026年7至9月", "2026-09-30", "2026-07-01", "quarter"},
		{"2026年全年", "2026-12-31", "2026-01-01", "fiscal_year"},
		{"预计盈利", "2026-09-30", "", "unknown"},
		{"2025年1-9月", "2026-09-30", "", "unknown"},
	} {
		start, basis := GuidanceOperatingPeriod(test.text, test.end)
		if start != test.start || basis != test.basis {
			t.Fatal(test, start, basis)
		}
	}
}
