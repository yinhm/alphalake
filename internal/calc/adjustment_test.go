package calc

import (
	"math"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

func adjustmentBar(id int64, day string) domain.DailyBar {
	t, _ := time.Parse("2006-01-02", day)
	return domain.DailyBar{InstrumentID: id, TradeDate: t, Source: "tdx"}
}

func adjustmentAction(id int64, day, typ string) domain.CorporateAction {
	t, _ := time.Parse("2006-01-02", day)
	return domain.CorporateAction{InstrumentID: id, ActionDate: t, ActionType: typ, Source: "tdx", SourceRecordID: day + ":" + typ}
}

func closeEnough(a, b float64) bool { return math.Abs(a-b) < 1e-12 }

func TestAdjustmentSegmentsCashDividendUsesAffineAdd(t *testing.T) {
	bars := []domain.DailyBar{
		adjustmentBar(1, "2026-06-26"),
		adjustmentBar(1, "2026-06-29"),
		adjustmentBar(1, "2026-06-30"),
	}
	action := adjustmentAction(1, "2026-06-28", "distribution") // Sunday -> Monday
	action.CashDividendPer10 = 10                               // one currency unit per share

	segments, err := AdjustmentSegments(bars, []domain.CorporateAction{action}, AdjustmentMethodAffineGBBQV1, "tdx")
	if err != nil {
		t.Fatalf("AdjustmentSegments() error = %v", err)
	}
	if len(segments) != 2 {
		t.Fatalf("segments = %#v", segments)
	}
	old, current := segments[0], segments[1]
	if old.EffectiveTo == nil || old.EffectiveTo.Format("2006-01-02") != "2026-06-26" || current.EffectiveFrom.Format("2006-01-02") != "2026-06-29" {
		t.Fatalf("segment dates old/current = %#v / %#v", old, current)
	}
	if !closeEnough(old.QFQMul, 1) || !closeEnough(old.QFQAdd, -1) {
		t.Fatalf("old QFQ = %.12g*x + %.12g", old.QFQMul, old.QFQAdd)
	}
	if !closeEnough(current.QFQMul, 1) || !closeEnough(current.QFQAdd, 0) {
		t.Fatalf("current QFQ = %.12g*x + %.12g", current.QFQMul, current.QFQAdd)
	}
	if !closeEnough(old.HFQMul, 1) || !closeEnough(old.HFQAdd, 0) || !closeEnough(current.HFQMul, 1) || !closeEnough(current.HFQAdd, 1) {
		t.Fatalf("HFQ old/current = %#v / %#v", old, current)
	}
}

func TestAdjustmentSegmentsCombinesBonusAndETFScale(t *testing.T) {
	bars := []domain.DailyBar{
		adjustmentBar(7, "2026-06-01"),
		adjustmentBar(7, "2026-06-02"),
		adjustmentBar(7, "2026-06-03"),
	}
	bonus := adjustmentAction(7, "2026-06-02", "distribution")
	bonus.BonusOrSplitPer10 = 10 // doubles units
	scale := adjustmentAction(7, "2026-06-02", "scale")
	scale.ScaleFactor = 2 // then doubles ETF units again

	segments, err := AdjustmentSegments(bars, []domain.CorporateAction{bonus, scale}, AdjustmentMethodAffineGBBQV1, "tdx")
	if err != nil {
		t.Fatalf("AdjustmentSegments() error = %v", err)
	}
	if len(segments) != 2 {
		t.Fatalf("len(segments) = %d, want 2", len(segments))
	}
	if !closeEnough(segments[0].QFQMul, 0.25) || !closeEnough(segments[1].HFQMul, 4) {
		t.Fatalf("combined coefficients = old QFQ %.12g, current HFQ %.12g", segments[0].QFQMul, segments[1].HFQMul)
	}
}

func TestAdjustmentSegmentsIgnoresUnverifiedCategory12PriceEffect(t *testing.T) {
	bars := []domain.DailyBar{adjustmentBar(9, "2026-06-01"), adjustmentBar(9, "2026-06-02")}
	action := adjustmentAction(9, "2026-06-02", "nontradable_share_scale")
	action.ScaleFactor = 3

	segments, err := AdjustmentSegments(bars, []domain.CorporateAction{action}, AdjustmentMethodAffineGBBQV1, "tdx")
	if err != nil {
		t.Fatalf("AdjustmentSegments() error = %v", err)
	}
	if len(segments) != 1 || !closeEnough(segments[0].QFQMul, 1) || !closeEnough(segments[0].QFQAdd, 0) || !closeEnough(segments[0].HFQMul, 1) || !closeEnough(segments[0].HFQAdd, 0) {
		t.Fatalf("unexpected segment = %#v", segments)
	}
}

func TestAdjustmentSegmentsIgnoresEventsBeforeFirstStoredPrice(t *testing.T) {
	bars := []domain.DailyBar{adjustmentBar(1, "2026-02-02"), adjustmentBar(1, "2026-02-03")}
	old := adjustmentAction(1, "1992-03-23", "distribution")
	old.RightsPrice, old.RightsPer10 = 3.56, 1
	later := adjustmentAction(1, "1993-05-24", "distribution")
	later.RightsPrice, later.RightsPer10 = 16, 1
	atStart := adjustmentAction(1, "2026-02-02", "distribution")
	atStart.RightsPrice, atStart.RightsPer10 = 20, 1
	current := adjustmentAction(1, "2026-02-03", "distribution")
	current.CashDividendPer10 = 10
	segments, err := AdjustmentSegments(bars, []domain.CorporateAction{old, later, atStart, current}, AdjustmentMethodAffineGBBQV1, "tdx")
	if err != nil {
		t.Fatal(err)
	}
	if len(segments) != 2 || !closeEnough(segments[0].QFQAdd, -1) || !closeEnough(segments[1].HFQAdd, 1) {
		t.Fatalf("historical events changed slice coefficients: %#v", segments)
	}
}
