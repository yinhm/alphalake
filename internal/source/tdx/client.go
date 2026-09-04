package tdx

import (
	"context"
	"fmt"
	"time"

	tdxlib "github.com/injoyai/tdx"
	"github.com/injoyai/tdx/protocol"
	"github.com/yinhm/alphalake/internal/domain"
)

// Client owns the provider SDK boundary. No injoyai/tdx type should escape this package.
type Client struct {
	raw *tdxlib.Client
}

func DialDefault() (*Client, error) {
	raw, err := tdxlib.DialDefault()
	if err != nil {
		return nil, fmt.Errorf("dial TDX: %w", err)
	}
	return &Client{raw: raw}, nil
}

func (c *Client) Close() {
	if c == nil || c.raw == nil {
		return
	}
	c.raw.Close()
}

func (c *Client) StockDailyBars(ctx context.Context, instrumentID int64, symbol string) ([]domain.DailyBar, error) {
	if c == nil || c.raw == nil {
		return nil, fmt.Errorf("TDX client is not initialized")
	}
	return fetchStockDailyBars(ctx, c.raw, instrumentID, symbol)
}

// StockDailyBarsSince fetches the boundary day again plus all newer bars. The
// inclusive boundary intentionally repairs a previously stored partial current-day
// bar by letting canonical upsert replace it.
func (c *Client) StockDailyBarsSince(ctx context.Context, instrumentID int64, symbol string, since time.Time) ([]domain.DailyBar, error) {
	if c == nil || c.raw == nil {
		return nil, fmt.Errorf("TDX client is not initialized")
	}
	return fetchStockDailyBarsSince(ctx, c.raw, instrumentID, symbol, since)
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
	return dailyBarsFromResponse(instrumentID, key.ProviderSymbol, resp, nil)
}

func fetchStockDailyBarsSince(ctx context.Context, c dailyKlineSinceClient, instrumentID int64, symbol string, since time.Time) ([]domain.DailyBar, error) {
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

	resp, err := c.GetKlineDayUntil(key.ProviderSymbol, func(k *protocol.Kline) bool {
		return k != nil && !k.Time.After(since)
	})
	if err != nil {
		return nil, fmt.Errorf("fetch TDX daily bars since %s for %s: %w", since.Format("2006-01-02"), key.ProviderSymbol, err)
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return dailyBarsFromResponse(instrumentID, key.ProviderSymbol, resp, &since)
}

func dailyBarsFromResponse(instrumentID int64, symbol string, resp *protocol.KlineResp, since *time.Time) ([]domain.DailyBar, error) {
	if resp == nil {
		return nil, nil
	}
	bars := make([]domain.DailyBar, 0, len(resp.List))
	for _, k := range resp.List {
		if k == nil || (since != nil && k.Time.Before(*since)) {
			continue
		}
		volume, err := NormalizeStockVolume(k.Volume)
		if err != nil {
			return nil, fmt.Errorf("normalize %s %s volume: %w", symbol, k.Time.Format("2006-01-02"), err)
		}
		bars = append(bars, domain.DailyBar{
			InstrumentID: instrumentID,
			TradeDate:    k.Time,
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
