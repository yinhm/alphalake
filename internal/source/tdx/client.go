package tdx

import (
	"context"
	"fmt"

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

type dailyKlineClient interface {
	GetKlineDayAll(code string) (*protocol.KlineResp, error)
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

	bars := make([]domain.DailyBar, 0, len(resp.List))
	for _, k := range resp.List {
		if k == nil {
			continue
		}
		volume, err := NormalizeStockVolume(k.Volume)
		if err != nil {
			return nil, fmt.Errorf("normalize %s %s volume: %w", key.ProviderSymbol, k.Time.Format("2006-01-02"), err)
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
