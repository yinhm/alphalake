package tdx

import "testing"

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
