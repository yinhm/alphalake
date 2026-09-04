package tdx

import (
	"context"
	"testing"
	"time"

	"github.com/injoyai/tdx/protocol"
)

type fakeDailySinceClient struct {
	resp            *protocol.KlineResp
	beforeStopped   bool
	boundaryStopped bool
	newerStopped    bool
}

func (f *fakeDailySinceClient) GetKlineDayUntil(_ string, stop func(*protocol.Kline) bool) (*protocol.KlineResp, error) {
	loc := time.FixedZone("UTC-10", -10*60*60)
	boundary := time.Date(2026, 9, 2, 15, 0, 0, 0, loc)
	f.beforeStopped = stop(&protocol.Kline{Time: boundary.AddDate(0, 0, -1)})
	f.boundaryStopped = stop(&protocol.Kline{Time: boundary})
	f.newerStopped = stop(&protocol.Kline{Time: boundary.AddDate(0, 0, 1)})
	return f.resp, nil
}

func TestFetchStockDailyBarsSinceUsesCalendarDateAndKeepsBoundary(t *testing.T) {
	loc := time.FixedZone("UTC-10", -10*60*60)
	sdkBoundary := time.Date(2026, 9, 2, 15, 0, 0, 0, loc)
	since := time.Date(2026, 9, 2, 0, 0, 0, 0, time.UTC)
	bar := func(day time.Time, close int64) *protocol.Kline {
		return &protocol.Kline{
			Open: protocol.Price(close - 10), High: protocol.Price(close + 10),
			Low: protocol.Price(close - 20), Close: protocol.Price(close),
			Volume: 100, Amount: protocol.Price(1000000), Time: day,
		}
	}
	fake := &fakeDailySinceClient{resp: &protocol.KlineResp{List: []*protocol.Kline{
		bar(sdkBoundary.AddDate(0, 0, -1), 10000),
		bar(sdkBoundary, 10100),
		bar(sdkBoundary.AddDate(0, 0, 1), 10200),
	}}}

	bars, err := fetchStockDailyBarsSince(context.Background(), fake, 7, "sh600519", since)
	if err != nil {
		t.Fatalf("fetchStockDailyBarsSince() error = %v", err)
	}
	if !fake.beforeStopped || fake.boundaryStopped || fake.newerStopped {
		t.Fatalf("stop predicate before/boundary/newer = %v/%v/%v, want true/false/false",
			fake.beforeStopped, fake.boundaryStopped, fake.newerStopped)
	}
	if len(bars) != 2 {
		t.Fatalf("len(bars) = %d, want boundary plus newer bar", len(bars))
	}
	wantNext := time.Date(2026, 9, 3, 0, 0, 0, 0, time.UTC)
	if !bars[0].TradeDate.Equal(since) || !bars[1].TradeDate.Equal(wantNext) {
		t.Fatalf("bar dates = %v, %v", bars[0].TradeDate, bars[1].TradeDate)
	}
}
