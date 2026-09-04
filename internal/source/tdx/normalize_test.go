package tdx

import (
	"testing"
	"time"
)

func TestNormalizeSymbol(t *testing.T) {
	tests := map[string]struct{ mic, ticker string }{
		"sh600519": {"XSHG", "600519"},
		"sz000001": {"XSHE", "000001"},
		"bj920000": {"XBSE", "920000"},
	}
	for input, want := range tests {
		got, err := NormalizeSymbol(input)
		if err != nil {
			t.Fatalf("NormalizeSymbol(%q): %v", input, err)
		}
		if got.ExchangeMIC != want.mic || got.Ticker != want.ticker {
			t.Fatalf("NormalizeSymbol(%q) = %#v", input, got)
		}
	}
	if _, err := NormalizeSymbol("xx600519"); err == nil {
		t.Fatal("expected unsupported prefix error")
	}
}

func TestNormalizeStockVolume(t *testing.T) {
	got, err := NormalizeStockVolume(12345)
	if err != nil {
		t.Fatal(err)
	}
	if got != 1234500 {
		t.Fatalf("got %d shares", got)
	}
}

func TestActionFromGBBQPreservesETFScale(t *testing.T) {
	a := ActionFromGBBQ(1, 11, 0, 0, 2.0, 0)
	if a.ActionType != "scale" || a.ScaleFactor != 2.0 || a.SourceCategory != 11 {
		t.Fatalf("unexpected action: %#v", a)
	}
}

func TestGBBQObservationCreatesVerifiedShareCapital(t *testing.T) {
	loc := time.FixedZone("UTC-10", -10*60*60)
	day := time.Date(2026, 6, 30, 15, 0, 0, 0, loc)
	observation, err := GBBQObservation("sh600519", day, 5, 100, 200, 123456789, 200000000)
	if err != nil {
		t.Fatalf("GBBQObservation() error = %v", err)
	}
	wantDate := time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC)
	if observation.Identifier.Value != "sh600519" || !observation.Action.ActionDate.Equal(wantDate) {
		t.Fatalf("observation identity/date = %#v", observation)
	}
	if observation.Action.ActionType != "share_capital_change" || observation.Action.SourceRecordID == "" {
		t.Fatalf("action = %#v", observation.Action)
	}
	if observation.ShareCapital == nil {
		t.Fatal("expected share-capital observation")
	}
	if observation.ShareCapital.FloatShares != 123456789 || observation.ShareCapital.TotalShares != 200000000 {
		t.Fatalf("share capital = %#v", observation.ShareCapital)
	}
	if observation.ShareCapital.SourceRecordID != observation.Action.SourceRecordID {
		t.Fatalf("source record IDs differ: %q/%q", observation.ShareCapital.SourceRecordID, observation.Action.SourceRecordID)
	}
}

func TestGBBQRecordIDDistinguishesSameDaySameCategoryEvents(t *testing.T) {
	day := time.Date(2026, 6, 30, 15, 0, 0, 0, time.Local)
	a, err := GBBQObservation("sh600519", day, 1, 1, 0, 0, 0)
	if err != nil {
		t.Fatal(err)
	}
	b, err := GBBQObservation("sh600519", day, 1, 2, 0, 0, 0)
	if err != nil {
		t.Fatal(err)
	}
	if a.Action.SourceRecordID == b.Action.SourceRecordID {
		t.Fatalf("same-day same-category events collided: %q", a.Action.SourceRecordID)
	}
}

func TestGBBQObservationDoesNotInventShareCapitalForUnverifiedCategory(t *testing.T) {
	day := time.Date(2026, 6, 30, 15, 0, 0, 0, time.Local)
	observation, err := GBBQObservation("sh600519", day, 6, 1, 2, 3, 4)
	if err != nil {
		t.Fatalf("GBBQObservation() error = %v", err)
	}
	if observation.Action.ActionType != "new_share_issue" {
		t.Fatalf("action type = %q", observation.Action.ActionType)
	}
	if observation.ShareCapital != nil {
		t.Fatalf("unexpected share-capital inference: %#v", observation.ShareCapital)
	}
}
