package tdx

import (
	"fmt"
	"math"
	"strings"
	"time"

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
	case 2:
		a.ActionType = "bonus_share_listing"
	case 3:
		a.ActionType = "nontradable_share_listing"
	case 4:
		a.ActionType = "unknown_share_change"
	case 5:
		a.ActionType = "share_capital_change"
	case 6:
		a.ActionType = "new_share_issue"
	case 7:
		a.ActionType = "share_repurchase"
	case 8:
		a.ActionType = "new_share_listing"
	case 9:
		a.ActionType = "transfer_share_listing"
	case 10:
		a.ActionType = "convertible_bond_listing"
	case 11:
		a.ActionType = "scale"
		a.ScaleFactor = c3
	case 12:
		a.ActionType = "nontradable_share_scale"
		a.ScaleFactor = c3
	case 13:
		a.ActionType = "call_warrant_distribution"
	case 14:
		a.ActionType = "put_warrant_distribution"
	default:
		a.ActionType = "unknown"
	}
	return a
}

// GBBQObservation converts one decoded TDX GBBQ event into provider-neutral
// canonical observations. Only categories with verified after-change share
// semantics produce ShareCapital; all categories still preserve the raw action.
func GBBQObservation(symbol string, eventDate time.Time, category int, c1, c2, c3, c4 float64) (domain.CorporateActionObservation, error) {
	key, err := NormalizeSymbol(symbol)
	if err != nil {
		return domain.CorporateActionObservation{}, err
	}
	if eventDate.IsZero() {
		return domain.CorporateActionObservation{}, fmt.Errorf("GBBQ event date is required for %s", key.ProviderSymbol)
	}

	recordID := fmt.Sprintf("%s:%s:%d", key.ProviderSymbol, eventDate.Format("2006-01-02"), category)
	action := ActionFromGBBQ(0, category, c1, c2, c3, c4)
	action.ActionDate = eventDate
	action.SourceRecordID = recordID
	observation := domain.CorporateActionObservation{
		Identifier: domain.Identifier{Provider: Provider, Type: "symbol", Value: key.ProviderSymbol},
		Action:     action,
	}

	if gbbqCarriesShareCapital(category) {
		floatShares, err := normalizeShareCount(c3)
		if err != nil {
			return domain.CorporateActionObservation{}, fmt.Errorf("normalize %s category %d float shares: %w", key.ProviderSymbol, category, err)
		}
		totalShares, err := normalizeShareCount(c4)
		if err != nil {
			return domain.CorporateActionObservation{}, fmt.Errorf("normalize %s category %d total shares: %w", key.ProviderSymbol, category, err)
		}
		observation.ShareCapital = &domain.ShareCapital{
			EffectiveDate:  eventDate,
			FloatShares:    floatShares,
			TotalShares:    totalShares,
			Source:          Provider,
			SourceCategory: category,
			SourceRecordID:  recordID,
		}
	}
	return observation, nil
}

func gbbqCarriesShareCapital(category int) bool {
	switch category {
	case 2, 3, 5, 7, 8, 9, 10:
		return true
	default:
		return false
	}
}

func normalizeShareCount(v float64) (int64, error) {
	if math.IsNaN(v) || math.IsInf(v, 0) || v < 0 || v > float64(math.MaxInt64) {
		return 0, fmt.Errorf("invalid share count %v", v)
	}
	return int64(math.Round(v)), nil
}
