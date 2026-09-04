package tdx

import (
	"fmt"
	"strings"

	"github.com/yinhm/alphalake/internal/domain"
)

const Provider = "tdx"

// InstrumentKey is AlphaLake's provider-neutral bootstrap description for one
// TDX symbol. It deliberately does not expose github.com/injoyai/tdx types.
type InstrumentKey struct {
	ProviderSymbol string
	Ticker         string
	ExchangeMIC    string
}

func NormalizeSymbol(symbol string) (InstrumentKey, error) {
	if len(symbol) != 8 {
		return InstrumentKey{}, fmt.Errorf("invalid TDX symbol %q", symbol)
	}
	prefix := strings.ToLower(symbol[:2])
	ticker := symbol[2:]
	for _, r := range ticker {
		if r < '0' || r > '9' {
			return InstrumentKey{}, fmt.Errorf("invalid TDX symbol %q", symbol)
		}
	}
	var mic string
	switch prefix {
	case "sh":
		mic = "XSHG"
	case "sz":
		mic = "XSHE"
	case "bj":
		mic = "XBSE"
	default:
		return InstrumentKey{}, fmt.Errorf("unsupported TDX market prefix %q", prefix)
	}
	return InstrumentKey{ProviderSymbol: prefix + ticker, Ticker: ticker, ExchangeMIC: mic}, nil
}

// NormalizeStockVolume converts TDX SDK stock volume expressed in hands into
// AlphaLake's canonical share/unit count. Index volume semantics are source-
// specific and must not pass through this helper.
func NormalizeStockVolume(hands int64) (int64, error) {
	if hands < 0 {
		return 0, fmt.Errorf("negative volume: %d", hands)
	}
	if hands > (1<<63-1)/100 {
		return 0, fmt.Errorf("volume overflows int64: %d hands", hands)
	}
	return hands * 100, nil
}

// ActionFromGBBQ keeps the source category and raw fields losslessly. Higher
// level action semantics are assigned here rather than delegated to a TDX SDK.
func ActionFromGBBQ(instrumentID int64, category int, c1, c2, c3, c4 float64) domain.CorporateAction {
	a := domain.CorporateAction{
		InstrumentID:   instrumentID,
		Source:         Provider,
		SourceCategory: category,
		RawC1:          c1,
		RawC2:          c2,
		RawC3:          c3,
		RawC4:          c4,
	}
	switch category {
	case 1:
		a.ActionType = "distribution"
		a.CashDividendPer10 = c1
		a.RightsPrice = c2
		a.BonusOrSplitPer10 = c3
		a.RightsPer10 = c4
	case 11, 12:
		a.ActionType = "scale"
		a.ScaleFactor = c3
	default:
		a.ActionType = "share_capital_change"
	}
	return a
}
