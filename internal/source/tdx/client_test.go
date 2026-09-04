package tdx

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/injoyai/tdx/protocol"
)

type fakeDailyClient struct {
	resp *protocol.KlineResp
	err  error
}

func (f fakeDailyClient) GetKlineDayAll(string) (*protocol.KlineResp, error) {
	return f.resp, f.err
}

type fakeDailySinceClient struct {
	list []*protocol.Kline
}

func (f fakeDailySinceClient) GetKlineDayUntil(_ string, stop func(k *protocol.Kline) bool) (*protocol.KlineResp, error) {
	out := make([]*protocol.Kline, 0, len(f.list))
	for _, k := range f.list {
		out = append(out, k)
		if stop(k) {
			break
		}
	}
	return &protocol.KlineResp{List: out}, nil
}

func TestFetchStockDailyBarsNormalizesSDKTypes(t *testing.T) {
	loc := time.FixedZone("UTC-10", -10*60*60)
	sdkTime := time.Date(2026, 9, 3, 15, 0, 0, 0, loc)
	fake := fakeDailyClient{resp: &protocol.KlineResp{List: []*protocol.Kline{{
		Open:   protocol.Price(10010),
		High:   protocol.Price(10250),
		Low:    protocol.Price(9950),
		Close:  protocol.Price(10120),
		Volume: 123,
		Amount: protocol.Price(987654321),
		Time:   sdkTime,
	}}}}

	bars, err := fetchStockDailyBars(context.Background(), fake, 42, "sh600519")
	if err != nil {
		t.Fatalf("fetchStockDailyBars() error = %v", err)
	}
	if len(bars) != 1 {
		t.Fatalf("len(bars) = %d, want 1", len(bars))
	}
	bar := bars[0]
	if bar.InstrumentID != 42 || bar.Source != Provider {
		t.Fatalf("identity/source = (%d, %q)", bar.InstrumentID, bar.Source)
	}
	if bar.Open != 10.01 || bar.Close != 10.12 {
		t.Fatalf("prices = open %.3f close %.3f", bar.Open, bar.Close)
	}
	if bar.Volume != 12300 {
		t.Fatalf("volume = %d, want 12300 shares", bar.Volume)
	}
	wantDate := time.Date(2026, 9, 3, 0, 0, 0, 0, time.UTC)
	if !bar.TradeDate.Equal(wantDate) {
		t.Fatalf("trade date = %v, want calendar date %v", bar.TradeDate, wantDate)
	}
}

func TestFetchStockDailyBarsSinceComparesCalendarDates(t *testing.T) {
	loc := time.FixedZone("UTC-10", -10*60*60)
	fake := fakeDailySinceClient{list: []*protocol.Kline{
		{Time: time.Date(2026, 9, 4, 15, 0, 0, 0, loc), Open: 1000, High: 1100, Low: 900, Close: 1050, Volume: 1},
		{Time: time.Date(2026, 9, 3, 15, 0, 0, 0, loc), Open: 1000, High: 1100, Low: 900, Close: 1050, Volume: 1},
		{Time: time.Date(2026, 9, 2, 15, 0, 0, 0, loc), Open: 1000, High: 1100, Low: 900, Close: 1050, Volume: 1},
	}}
	boundary := time.Date(2026, 9, 3, 0, 0, 0, 0, time.UTC)
	bars, err := fetchStockDailyBarsSince(context.Background(), fake, 1, "sh600519", boundary)
	if err != nil {
		t.Fatal(err)
	}
	if len(bars) != 2 {
		t.Fatalf("len(bars)=%d, want boundary plus newer day", len(bars))
	}
	if !bars[0].TradeDate.Equal(time.Date(2026, 9, 4, 0, 0, 0, 0, time.UTC)) || !bars[1].TradeDate.Equal(boundary) {
		t.Fatalf("unexpected canonical dates: %#v", bars)
	}
}

func TestFetchStockDailyBarsHonorsCanceledContext(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	_, err := fetchStockDailyBars(ctx, fakeDailyClient{}, 1, "sh600519")
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %v, want context.Canceled", err)
	}
}
