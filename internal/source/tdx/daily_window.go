package tdx

import (
	"context"
	"fmt"
	"time"

	"github.com/injoyai/tdx/protocol"
)

type dailyPageClient interface {
	GetKlineDay(string, uint16, uint16) (*protocol.KlineResp, error)
}

// Daily dates are unique. Calendar distance plus a boundary row suffices for
// recent windows; older history keeps the SDK's 800-row page size.
func windowPageSize(start, now time.Time) uint16 {
	days := int(canonicalTradeDate(now).Sub(canonicalTradeDate(start)) / (24 * time.Hour))
	return uint16(min(800, max(16, days+2)))
}

func klineDayUntil(ctx context.Context, client dailyPageClient, code string, size uint16, stop func(*protocol.Kline) bool) (*protocol.KlineResp, error) {
	if size < 1 || size > 800 {
		return nil, fmt.Errorf("invalid daily page size")
	}
	out := &protocol.KlineResp{}
	for offset := 0; offset <= 65535; offset += int(size) {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		page, err := client.GetKlineDay(code, uint16(offset), size)
		if err != nil {
			return nil, err
		}
		if page == nil || int(page.Count) != len(page.List) || page.Count > size {
			return nil, fmt.Errorf("invalid daily page count")
		}
		for i, row := range page.List {
			if row == nil || i > 0 && !canonicalTradeDate(page.List[i-1].Time).Before(canonicalTradeDate(row.Time)) {
				return nil, fmt.Errorf("invalid daily page order")
			}
		}
		if len(out.List) > 0 && len(page.List) > 0 {
			last := page.List[len(page.List)-1]
			if !canonicalTradeDate(last.Time).Before(canonicalTradeDate(out.List[0].Time)) {
				return nil, fmt.Errorf("overlapping daily pages")
			}
			out.List[0].Last = last.Close
		}
		for i := len(page.List) - 1; i >= 0; i-- {
			if stop(page.List[i]) {
				out.List = append(page.List[i:], out.List...)
				if len(out.List) > 65535 {
					return nil, fmt.Errorf("daily response exceeds protocol limit")
				}
				out.Count = uint16(len(out.List))
				return out, nil
			}
		}
		out.List = append(page.List, out.List...)
		if len(out.List) > 65535 {
			return nil, fmt.Errorf("daily response exceeds protocol limit")
		}
		out.Count = uint16(len(out.List))
		if page.Count < size {
			return out, nil
		}
	}
	return nil, fmt.Errorf("daily page offset exhausted")
}
