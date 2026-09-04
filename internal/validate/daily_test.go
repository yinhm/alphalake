package validate

import (
	"math"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

func TestDailyBarsAcceptsValidBar(t *testing.T) {
	bars := []domain.DailyBar{{
		InstrumentID: 1,
		TradeDate:    time.Date(2026, 9, 3, 0, 0, 0, 0, time.UTC),
		Open:         10,
		High:         12,
		Low:          9,
		Close:        11,
		Volume:       1000,
		Amount:       11000,
		Source:       "tdx",
	}}
	if got := DailyBars(bars); len(got) != 0 {
		t.Fatalf("DailyBars() violations = %#v, want none", got)
	}
}

func TestDailyBarsFindsStructuralViolations(t *testing.T) {
	bars := []domain.DailyBar{{
		InstrumentID: 1,
		TradeDate:    time.Date(2026, 9, 3, 0, 0, 0, 0, time.UTC),
		Open:         10,
		High:         9,
		Low:          11,
		Close:        math.NaN(),
		Volume:       -1,
		Amount:       -10,
		Source:       "tdx",
	}}
	violations := DailyBars(bars)
	want := map[string]bool{
		"daily.price_finite":       false,
		"daily.volume_nonnegative": false,
		"daily.amount_nonnegative": false,
	}
	for _, v := range violations {
		if _, ok := want[v.RuleCode]; ok {
			want[v.RuleCode] = true
		}
	}
	for rule, found := range want {
		if !found {
			t.Fatalf("missing validation rule %q in %#v", rule, violations)
		}
	}
}
