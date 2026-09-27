package tdx

import (
	"context"
	"fmt"
	"time"

	"github.com/injoyai/tdx/protocol"
	"github.com/yinhm/alphalake/internal/domain"
)

func (c *Client) StockDailyBars(ctx context.Context, instrumentID int64, symbol string) ([]domain.DailyBar, error) {
	if c == nil {
		return nil, fmt.Errorf("TDX client is not initialized")
	}
	return fetchStockDailyBars(ctx, c.requests(ctx), instrumentID, symbol)
}

// StockDailyBarsSince fetches the boundary calendar day again plus all newer
// bars. The comparison is date-only: SDK Kline.Time is an exchange observation
// encoded in time.Local, while AlphaLake's canonical TradeDate is always UTC
// midnight carrying only the exchange-local Y/M/D fields.
func (c *Client) StockDailyBarsSince(ctx context.Context, instrumentID int64, symbol string, since time.Time) ([]domain.DailyBar, error) {
	if c == nil {
		return nil, fmt.Errorf("TDX client is not initialized")
	}
	return fetchStockDailyBarsSince(ctx, c.requests(ctx), instrumentID, symbol, since)
}

type dailyKlineClient interface {
	GetKlineDayAll(code string) (*protocol.KlineResp, error)
}

type dailyKlineSinceClient interface {
	GetKlineDayUntil(code string, f func(k *protocol.Kline) bool) (*protocol.KlineResp, error)
}

func fetchStockDailyBars(ctx context.Context, c dailyKlineClient, instrumentID int64, symbol string) ([]domain.DailyBar, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	key, err := NormalizeSymbol(symbol)
	if err != nil {
		return nil, err
	}

	resp, err := c.GetKlineDayAll(key.ProviderSymbol)
	if err != nil {
		return nil, fmt.Errorf("fetch TDX daily bars for %s: %w", key.ProviderSymbol, err)
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return dailyBarsFromResponse(instrumentID, key.ProviderSymbol, resp, nil, nil)
}

func fetchStockDailyBarsSince(ctx context.Context, c dailyKlineSinceClient, instrumentID int64, symbol string, since time.Time) ([]domain.DailyBar, error) {
	return fetchStockDailyRange(ctx, c, instrumentID, symbol, since, nil)
}
func fetchStockDailyRange(ctx context.Context, c dailyKlineSinceClient, instrumentID int64, symbol string, since time.Time, until *time.Time) ([]domain.DailyBar, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if since.IsZero() {
		return nil, fmt.Errorf("incremental boundary is required")
	}
	key, err := NormalizeSymbol(symbol)
	if err != nil {
		return nil, err
	}
	sinceDate := canonicalTradeDate(since)

	resp, err := c.GetKlineDayUntil(key.ProviderSymbol, func(k *protocol.Kline) bool {
		if k == nil {
			return false
		}
		// GetKlineDayUntil includes the matching bar before stopping. Stop only
		// once we reach a day strictly before the inclusive boundary.
		return canonicalTradeDate(k.Time).Before(sinceDate)
	})
	if err != nil {
		return nil, fmt.Errorf("fetch TDX daily bars since %s for %s: %w", sinceDate.Format("2006-01-02"), key.ProviderSymbol, err)
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return dailyBarsFromResponse(instrumentID, key.ProviderSymbol, resp, &sinceDate, until)
}

func dailyBarsFromResponse(instrumentID int64, symbol string, resp *protocol.KlineResp, since, until *time.Time) ([]domain.DailyBar, error) {
	if resp == nil {
		return nil, nil
	}
	bars := make([]domain.DailyBar, 0, len(resp.List))
	for _, k := range resp.List {
		if k == nil {
			continue
		}
		tradeDate := canonicalTradeDate(k.Time)
		if (since != nil && tradeDate.Before(*since)) || (until != nil && tradeDate.After(*until)) {
			continue
		}
		volume, err := NormalizeStockVolume(k.Volume)
		if err != nil {
			return nil, fmt.Errorf("normalize %s %s volume: %w", symbol, tradeDate.Format("2006-01-02"), err)
		}
		bars = append(bars, domain.DailyBar{
			InstrumentID: instrumentID,
			TradeDate:    tradeDate,
			Open:         k.Open.Float64(),
			High:         k.High.Float64(),
			Low:          k.Low.Float64(),
			Close:        k.Close.Float64(),
			Volume:       volume,
			Amount:       k.Amount.Float64(),
			UpCount:      int64(k.UpCount),
			DownCount:    int64(k.DownCount),
			Source:       Provider,
		})
	}
	return bars, nil
}

// canonicalTradeDate intentionally copies calendar fields rather than calling
// t.UTC(). The TDX SDK encodes a market-local observation date using time.Local;
// timezone conversion could therefore move the date. Within AlphaLake a DATE is
// represented in Go as UTC midnight with no instant semantics.
func canonicalTradeDate(t time.Time) time.Time {
	y, m, d := t.Date()
	return time.Date(y, m, d, 0, 0, 0, 0, time.UTC)
}

// StockDailyBarsWindow stops paging at the lower date and discards newer rows
// outside the requested interval. SDK pages can contain additional transport rows.
func (c *Client) StockDailyBarsWindow(ctx context.Context, id int64, symbol string, start, end time.Time) ([]domain.DailyBar, error) {
	if c == nil {
		return nil, fmt.Errorf("TDX client is not initialized")
	}
	return fetchStockDailyBarsWindow(ctx, c.requests(ctx), id, symbol, start, end)
}
func fetchStockDailyBarsWindow(ctx context.Context, c dailyKlineSinceClient, id int64, symbol string, start, end time.Time) ([]domain.DailyBar, error) {
	if start.IsZero() || end.IsZero() || canonicalTradeDate(start).After(canonicalTradeDate(end)) {
		return nil, fmt.Errorf("valid daily date window required")
	}
	end = canonicalTradeDate(end)
	return fetchStockDailyRange(ctx, c, id, symbol, start, &end)
}
