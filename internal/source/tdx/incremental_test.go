package tdx

import (
	"context"
	"testing"
	"time"

	"github.com/injoyai/tdx/protocol"
)

type fakeDailySinceClient struct {
	resp         *protocol.KlineResp
	boundaryHit  bool
	newerStopped bool
}

func (f *fakeDailySinceClient) GetKlineDayUntil(_ string, stop func(*protocol.Kline) bool) (*protocol.KlineResp, error) {
	boundary := time.Date(2026, 9, 2, 0, 0, 0, 0, time.Local)
	f.boundaryHit = stop(&protocol.Kline{Time: boundary})
	f.newerStopped = stop(&protocol.Kline{Time: boundary.AddDate(0, 0, 1)})
	return f.resp, nil
}

func TestFetchStockDailyBarsSinceKeepsBoundaryAndNewerBars(t *testing.T) {
	since := time.Date(2026, 9, 2, 0, 0, 0, 0, time.Local)
	bar := func(day time.Time, close int64) *protocol.Kline {
		return &protocol.Kline{
			Open: protocol.Price(close - 10), High: protocol.Price(close + 10),
			Low: protocol.Price(close - 20), Close: protocol.Price(close),
			Volume: 100, Amount: protocol.Price(1000000), Time: day,
		}
	}
	fake := &fakeDailySinceClient{resp: &protocol.KlineResp{List: []*protocol.Kline{
		bar(since.AddDate(0, 0, -1), 10000),
		bar(since, 10100),
		bar(since.AddDate(0, 0, 1), 10200),
	}}}

	bars, err := fetchStockDailyBarsSince(context.Background(), fake, 7, "sh600519", since)
	if err != nil {
		t.Fatalf("fetchStockDailyBarsSince() error = %v", err)
	}
	if !fake.boundaryHit || fake.newerStopped {
		t.Fatalf("stop predicate boundary/newer = %v/%v, want true/false", fake.boundaryHit, fake.newerStopped)
	}
	if len(bars) != 2 {
		t.Fatalf("len(bars) = %d, want boundary plus newer bar", len(bars))
	}
	if !bars[0].TradeDate.Equal(since) || !bars[1].TradeDate.Equal(since.AddDate(0, 0, 1)) {
		t.Fatalf("bar dates = %v, %v", bars[0].TradeDate, bars[1].TradeDate)
	}
}
