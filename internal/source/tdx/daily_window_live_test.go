//go:build full

package tdx

import (
	"context"
	"encoding/json"
	tdxlib "github.com/injoyai/tdx"
	"github.com/injoyai/tdx/protocol"
	"github.com/yinhm/alphalake/internal/domain"
	"os"
	"reflect"
	"testing"
	"time"
)

// Explicit online acceptance; normal CI keeps deterministic archived/mocked tests.
func TestLiveWindowRequestEquivalence(t *testing.T) {
	output := os.Getenv("ALPHALAKE_TDX_WINDOW_PROBE")
	if output == "" {
		t.Skip("explicit live window probe not requested")
	}
	client, err := DialDefault()
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	end := domain.CompletedMarketDate(time.Now())
	start := end.AddDate(0, 0, -14)
	results := []map[string]any{}
	for i, symbol := range []string{"sz300866", "sh600519", "sh688099"} {
		ctx, cancel := context.WithTimeout(t.Context(), 180*time.Second)
		var old, new []domain.DailyBar
		var oldSeconds, newSeconds float64
		legacy := func() {
			before := time.Now()
			stop := func(k *protocol.Kline) bool { return canonicalTradeDate(k.Time).Before(start) }
			response, e := withNode(ctx, client, "probe-legacy/"+symbol, func(c *tdxlib.Client) (*protocol.KlineResp, error) {
				v, e := c.GetKlineDayUntil(symbol, stop)
				return dailyWindowResponse(v, e, stop)
			})
			if e != nil {
				t.Fatal(e)
			}
			old, e = dailyBarsFromResponse(1, symbol, response, &start, &end)
			if e != nil {
				t.Fatal(e)
			}
			oldSeconds = time.Since(before).Seconds()
		}
		bounded := func() {
			before := time.Now()
			new, err = client.StockDailyBarsWindow(ctx, 1, symbol, start, end)
			if err != nil {
				t.Fatal(err)
			}
			newSeconds = time.Since(before).Seconds()
		}
		if i%2 == 0 {
			legacy()
			bounded()
		} else {
			bounded()
			legacy()
		}
		cancel()
		if len(new) == 0 || !reflect.DeepEqual(old, new) {
			t.Fatal("bounded transport changed observations", symbol, old, new)
		}
		results = append(results, map[string]any{"symbol": symbol, "date": end.Format("2006-01-02"), "bars": new, "legacy_requested_rows": 800,
			"bounded_requested_rows": windowPageSize(start, time.Now()), "legacy_seconds": oldSeconds, "bounded_seconds": newSeconds})
	}
	raw, err := json.MarshalIndent(map[string]any{"status": "verified", "data_written": false, "results": results}, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(output, raw, 0600); err != nil {
		t.Fatal(err)
	}
}
