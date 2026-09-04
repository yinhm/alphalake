package tdx

import (
	"context"
	"fmt"

	"github.com/injoyai/tdx/protocol"
	"github.com/yinhm/alphalake/internal/domain"
)

type gbbqClient interface {
	GetGbbq(code string) (*protocol.GbbqResp, error)
}

// CorporateActions fetches one TDX symbol's complete GBBQ history and converts
// it to AlphaLake-neutral observations. The canonical instrument ID is resolved
// later by the ingest layer through the provider identifier.
func (c *Client) CorporateActions(ctx context.Context, symbol string) ([]domain.CorporateActionObservation, error) {
	if c == nil || c.raw == nil {
		return nil, fmt.Errorf("TDX client is not initialized")
	}
	return fetchCorporateActions(ctx, c.raw, symbol)
}

func fetchCorporateActions(ctx context.Context, c gbbqClient, symbol string) ([]domain.CorporateActionObservation, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	key, err := NormalizeSymbol(symbol)
	if err != nil {
		return nil, err
	}

	resp, err := c.GetGbbq(key.ProviderSymbol)
	if err != nil {
		return nil, fmt.Errorf("fetch TDX GBBQ for %s: %w", key.ProviderSymbol, err)
	}
	if resp == nil {
		return nil, nil
	}

	observations := make([]domain.CorporateActionObservation, 0, len(resp.List))
	for _, event := range resp.List {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		if event == nil {
			continue
		}
		observation, err := GBBQObservation(
			key.ProviderSymbol,
			event.Time,
			event.Category,
			event.C1,
			event.C2,
			event.C3,
			event.C4,
		)
		if err != nil {
			return nil, fmt.Errorf("normalize TDX GBBQ for %s: %w", key.ProviderSymbol, err)
		}
		observations = append(observations, observation)
	}
	return observations, nil
}
