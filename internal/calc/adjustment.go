package calc

import (
	"errors"
	"fmt"
	"math"
	"sort"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

const AdjustmentMethodAffineGBBQV1 = "affine_gbbq_v1"

const coefficientTolerance = 1e-12

type eventAdjustment struct {
	cashDividend float64
	bonusPer10   float64
	rightsPer10  float64
	rightsPrice  float64
	scale        float64
}

// AdjustmentSegments derives reproducible affine QFQ/HFQ transformations from
// canonical unadjusted daily bars plus corporate actions. It intentionally uses
// only action semantics that have been verified for price adjustment:
// distribution (TDX category 1) and scale (TDX category 11).
func AdjustmentSegments(bars []domain.DailyBar, actions []domain.CorporateAction, method, source string) ([]domain.AdjustmentSegment, error) {
	if len(bars) == 0 {
		return nil, nil
	}
	method = strings.TrimSpace(method)
	source = strings.TrimSpace(source)
	if method == "" {
		return nil, errors.New("adjustment method is required")
	}
	if source == "" {
		return nil, errors.New("adjustment source is required")
	}

	bs := append([]domain.DailyBar(nil), bars...)
	sort.SliceStable(bs, func(i, j int) bool { return dateKey(bs[i].TradeDate) < dateKey(bs[j].TradeDate) })
	instrumentID := bs[0].InstrumentID
	if instrumentID <= 0 {
		return nil, errors.New("daily bars require a positive instrument ID")
	}
	for i, bar := range bs {
		if bar.InstrumentID != instrumentID {
			return nil, fmt.Errorf("bar %d instrument ID %d does not match %d", i, bar.InstrumentID, instrumentID)
		}
		if bar.TradeDate.IsZero() {
			return nil, fmt.Errorf("bar %d has zero trade date", i)
		}
		if i > 0 && dateKey(bs[i-1].TradeDate) == dateKey(bar.TradeDate) {
			return nil, fmt.Errorf("duplicate trade date %s", dateOnly(bar.TradeDate).Format("2006-01-02"))
		}
	}

	as := append([]domain.CorporateAction(nil), actions...)
	sort.SliceStable(as, func(i, j int) bool {
		ki, kj := dateKey(as[i].ActionDate), dateKey(as[j].ActionDate)
		if ki != kj {
			return ki < kj
		}
		return as[i].SourceRecordID < as[j].SourceRecordID
	})

	events := make(map[int]*eventAdjustment)
	for i, action := range as {
		if action.InstrumentID != instrumentID {
			return nil, fmt.Errorf("action %d instrument ID %d does not match %d", i, action.InstrumentID, instrumentID)
		}
		if action.Source != source {
			return nil, fmt.Errorf("action %d source %q does not match %q", i, action.Source, source)
		}
		if action.ActionDate.IsZero() {
			return nil, fmt.Errorf("action %d has zero action date", i)
		}
		if action.ActionType != "distribution" && action.ActionType != "scale" {
			continue
		}

		key := dateKey(action.ActionDate)
		idx := sort.Search(len(bs), func(j int) bool { return dateKey(bs[j].TradeDate) >= key })
		if idx >= len(bs) {
			continue
		}
		event := events[idx]
		if event == nil {
			event = &eventAdjustment{scale: 1}
			events[idx] = event
		}

		switch action.ActionType {
		case "distribution":
			event.cashDividend += action.CashDividendPer10
			event.bonusPer10 += action.BonusOrSplitPer10
			event.rightsPer10 += action.RightsPer10
			if action.RightsPrice != 0 {
				if event.rightsPrice != 0 && math.Abs(event.rightsPrice-action.RightsPrice) > coefficientTolerance {
					return nil, fmt.Errorf("conflicting rights prices on %s: %.12g vs %.12g", dateOnly(action.ActionDate).Format("2006-01-02"), event.rightsPrice, action.RightsPrice)
				}
				event.rightsPrice = action.RightsPrice
			}
		case "scale":
			if action.ScaleFactor <= 0 {
				return nil, fmt.Errorf("invalid scale factor %.12g on %s", action.ScaleFactor, dateOnly(action.ActionDate).Format("2006-01-02"))
			}
			event.scale *= action.ScaleFactor
		}
	}

	n := len(bs)
	qMul := make([]float64, n)
	qAdd := make([]float64, n)
	qMul[n-1] = 1
	qAdd[n-1] = 0

	for i := n - 1; i > 0; i-- {
		afterMul, afterAdd := qMul[i], qAdd[i]
		event := events[i]
		if event == nil {
			qMul[i-1], qAdd[i-1] = afterMul, afterAdd
			continue
		}
		m, c, err := event.affine()
		if err != nil {
			return nil, fmt.Errorf("adjustment event at %s: %w", dateOnly(bs[i].TradeDate).Format("2006-01-02"), err)
		}
		qMul[i-1] = afterMul / m
		qAdd[i-1] = afterAdd - afterMul*c/m
	}

	baseMul, baseAdd := qMul[0], qAdd[0]
	if math.Abs(baseMul) < coefficientTolerance {
		return nil, errors.New("earliest QFQ multiplier is zero")
	}
	hMul := make([]float64, n)
	hAdd := make([]float64, n)
	for i := range bs {
		hMul[i] = qMul[i] / baseMul
		hAdd[i] = (qAdd[i] - baseAdd) / baseMul
	}

	segments := make([]domain.AdjustmentSegment, 0, len(events)+1)
	start := 0
	for i := 1; i < n; i++ {
		if sameCoefficients(qMul[i-1], qAdd[i-1], hMul[i-1], hAdd[i-1], qMul[i], qAdd[i], hMul[i], hAdd[i]) {
			continue
		}
		to := dateOnly(bs[i-1].TradeDate)
		segments = append(segments, domain.AdjustmentSegment{
			InstrumentID: instrumentID,
			EffectiveFrom: dateOnly(bs[start].TradeDate),
			EffectiveTo: &to,
			QFQMul: qMul[start], QFQAdd: qAdd[start],
			HFQMul: hMul[start], HFQAdd: hAdd[start],
			Method: method, Source: source,
		})
		start = i
	}
	segments = append(segments, domain.AdjustmentSegment{
		InstrumentID: instrumentID,
		EffectiveFrom: dateOnly(bs[start].TradeDate),
		EffectiveTo: nil,
		QFQMul: qMul[start], QFQAdd: qAdd[start],
		HFQMul: hMul[start], HFQAdd: hAdd[start],
		Method: method, Source: source,
	})
	return segments, nil
}

func (e *eventAdjustment) affine() (m, c float64, err error) {
	if e == nil {
		return 1, 0, nil
	}
	m = (10 + e.bonusPer10 + e.rightsPer10) / 10
	if m <= 0 {
		return 0, 0, fmt.Errorf("non-positive distribution multiplier %.12g", m)
	}
	c = (e.cashDividend - e.rightsPer10*e.rightsPrice) / 10
	if e.scale == 0 {
		e.scale = 1
	}
	if e.scale <= 0 {
		return 0, 0, fmt.Errorf("non-positive scale %.12g", e.scale)
	}
	m *= e.scale
	return m, c, nil
}

func sameCoefficients(a1, b1, c1, d1, a2, b2, c2, d2 float64) bool {
	return almostEqual(a1, a2) && almostEqual(b1, b2) && almostEqual(c1, c2) && almostEqual(d1, d2)
}

func almostEqual(a, b float64) bool {
	scale := math.Max(1, math.Max(math.Abs(a), math.Abs(b)))
	return math.Abs(a-b) <= coefficientTolerance*scale
}

func dateKey(t time.Time) int {
	y, m, d := t.Date()
	return y*10000 + int(m)*100 + d
}

func dateOnly(t time.Time) time.Time {
	y, m, d := t.Date()
	return time.Date(y, m, d, 0, 0, 0, 0, time.UTC)
}
