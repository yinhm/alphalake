package tdx

import (
	"context"
	"errors"
	"testing"
	"time"

	tdxlib "github.com/injoyai/tdx"
	"github.com/injoyai/tdx/protocol"
)

func TestDailyEmptyAndOlderResponsesUseThreeNodeBudget(t *testing.T) {
	boundary := time.Date(2026, 9, 16, 0, 0, 0, 0, time.UTC)
	stop := func(k *protocol.Kline) bool { return k.Time.Before(boundary) }
	valid := &protocol.KlineResp{List: []*protocol.Kline{{Time: boundary}}}
	old := &protocol.KlineResp{List: []*protocol.Kline{{Time: boundary.AddDate(0, 0, -1)}}}
	for _, recoverLast := range []bool{true, false} {
		c, err := DialHosts([]string{"127.0.0.1", "127.0.0.2", "127.0.0.3", "127.0.0.4"})
		if err != nil {
			t.Fatal(err)
		}
		c.dial = func(context.Context, string) (*nodeSession, error) { return &nodeSession{stop: func() {}}, nil }
		calls := 0
		v, err := withNode(context.Background(), c, "daily-window", func(*tdxlib.Client) (*protocol.KlineResp, error) {
			calls++
			if calls == 1 {
				return dailyResponse(nil, nil)
			}
			if calls == 3 && recoverLast {
				return dailyWindowResponse(valid, nil, stop)
			}
			return dailyWindowResponse(old, nil, stop)
		})
		c.Close()
		if calls != 3 || (err == nil) != recoverLast || (recoverLast && v != valid) {
			t.Fatal(calls, v, err, recoverLast)
		}
	}
	transport := errors.New("original transport failure")
	if _, err := dailyWindowResponse(valid, transport, stop); err != transport {
		t.Fatal(err)
	}
	if _, err := dailyResponse(&protocol.KlineResp{}, nil); err == nil {
		t.Fatal("empty response accepted")
	}
}
