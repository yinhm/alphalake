package domain

import (
	"testing"
	"time"
)

func TestCompletedMarketDate(t *testing.T) {
	for _, c := range []struct{ at, want string }{
		{"2026-09-30T06:59:59Z", "2026-09-29"},
		{"2026-09-30T07:00:00Z", "2026-09-30"},
		{"2026-09-30T16:00:00Z", "2026-09-30"},
	} {
		at, err := time.Parse(time.RFC3339, c.at)
		if err != nil {
			t.Fatal(err)
		}
		if got := CompletedMarketDate(at).Format("2006-01-02"); got != c.want {
			t.Fatal(c.at, got, c.want)
		}
	}
}
