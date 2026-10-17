package tdx

import (
	"context"
	"github.com/injoyai/tdx/protocol"
	"reflect"
	"testing"
	"time"
)

type pageFixture struct {
	rows      []*protocol.Kline
	calls     []uint16
	malformed *protocol.KlineResp
}

func (f *pageFixture) GetKlineDay(_ string, offset, count uint16) (*protocol.KlineResp, error) {
	f.calls = append(f.calls, count)
	if f.malformed != nil {
		return f.malformed, nil
	}
	end := max(0, len(f.rows)-int(offset))
	start := max(0, end-int(count))
	return &protocol.KlineResp{Count: uint16(end - start), List: f.rows[start:end]}, nil
}
func TestDailyWindowUsesBoundedPagesWithoutChangingBars(t *testing.T) {
	now := time.Date(2026, 10, 17, 0, 0, 0, 0, time.UTC)
	for _, age := range []int{0, 900} {
		f := &pageFixture{}
		for i := 1999; i >= 0; i-- {
			f.rows = append(f.rows, &protocol.Kline{Time: now.AddDate(0, 0, -i), Close: 12345, Volume: 100})
		}
		end := now.AddDate(0, 0, -age)
		start := end.AddDate(0, 0, -14)
		fetch := func(size uint16) []int64 {
			resp, err := klineDayUntil(t.Context(), f, "sh600004", size, func(k *protocol.Kline) bool { return canonicalTradeDate(k.Time).Before(start) })
			if err != nil {
				t.Fatal(err)
			}
			bars, err := dailyBarsFromResponse(1, "sh600004", resp, &start, &end)
			if err != nil {
				t.Fatal(err)
			}
			days := []int64{}
			for _, bar := range bars {
				days = append(days, bar.TradeDate.Unix())
			}
			return days
		}
		bounded := fetch(windowPageSize(start, now))
		legacy := fetch(800)
		if len(bounded) != 15 || !reflect.DeepEqual(bounded, legacy) {
			t.Fatal(bounded, legacy)
		}
		if age == 0 && f.calls[0] != 16 {
			t.Fatal("recent request still fetches excess history", f.calls)
		}
		if age == 900 && f.calls[0] != 800 {
			t.Fatal("historical window uses tiny pages", f.calls)
		}
	}
}
func TestDailyWindowRejectsMalformedPagesAndCancellation(t *testing.T) {
	for _, page := range []*protocol.KlineResp{{Count: 1}, {Count: 1, List: []*protocol.Kline{nil}},
		{Count: 2, List: []*protocol.Kline{{Time: time.Now()}, {Time: time.Now().AddDate(0, 0, -1)}}}} {
		_, err := klineDayUntil(t.Context(), &pageFixture{malformed: page}, "sh600004", 16, func(*protocol.Kline) bool { return false })
		if err == nil {
			t.Fatal("malformed daily page accepted", page)
		}
	}
	ctx, cancel := context.WithCancel(t.Context())
	cancel()
	f := &pageFixture{}
	if _, err := klineDayUntil(ctx, f, "sh600004", 16, func(*protocol.Kline) bool { return false }); err == nil || len(f.calls) != 0 {
		t.Fatal(err, f.calls)
	}
}
