package validate

import (
	"fmt"
	"math"
	"strings"

	"github.com/yinhm/alphalake/internal/domain"
)

// Violation is a provider-neutral data-quality finding. Ingestion may choose to
// persist these findings and reject the affected batch.
type Violation struct {
	RuleCode   string
	Severity   string
	SubjectKey string
	Details    string
}

// DailyBars validates canonical, unadjusted OHLCV records before persistence.
// The rules are deliberately structural; provider-specific market rules belong
// in the adapter layer.
func DailyBars(bars []domain.DailyBar) []Violation {
	var out []Violation
	for i, bar := range bars {
		subject := dailySubject(bar, i)
		if bar.InstrumentID <= 0 {
			out = append(out, violation("daily.instrument_id", subject, "instrument ID must be positive"))
		}
		if bar.TradeDate.IsZero() {
			out = append(out, violation("daily.trade_date", subject, "trade date is required"))
		}
		if strings.TrimSpace(bar.Source) == "" {
			out = append(out, violation("daily.source", subject, "source is required"))
		}

		if !finite(bar.Open) || !finite(bar.High) || !finite(bar.Low) || !finite(bar.Close) {
			out = append(out, violation("daily.price_finite", subject, "OHLC values must be finite"))
		} else {
			if bar.High < bar.Open || bar.High < bar.Low || bar.High < bar.Close {
				out = append(out, violation("daily.high_bound", subject,
					fmt.Sprintf("high %.6f is below another OHLC value", bar.High)))
			}
			if bar.Low > bar.Open || bar.Low > bar.High || bar.Low > bar.Close {
				out = append(out, violation("daily.low_bound", subject,
					fmt.Sprintf("low %.6f is above another OHLC value", bar.Low)))
			}
		}
		if bar.Volume < 0 {
			out = append(out, violation("daily.volume_nonnegative", subject,
				fmt.Sprintf("volume %d is negative", bar.Volume)))
		}
		if !finite(bar.Amount) || bar.Amount < 0 {
			out = append(out, violation("daily.amount_nonnegative", subject,
				fmt.Sprintf("amount %.6f must be finite and non-negative", bar.Amount)))
		}
	}
	return out
}

func violation(rule, subject, details string) Violation {
	return Violation{RuleCode: rule, Severity: "error", SubjectKey: subject, Details: details}
}

func dailySubject(bar domain.DailyBar, index int) string {
	if bar.InstrumentID > 0 && !bar.TradeDate.IsZero() {
		return fmt.Sprintf("%d:%s", bar.InstrumentID, bar.TradeDate.Format("2006-01-02"))
	}
	return fmt.Sprintf("batch[%d]", index)
}

func finite(v float64) bool {
	return !math.IsNaN(v) && !math.IsInf(v, 0)
}
