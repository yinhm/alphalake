package tdx

import (
	"context"
	"fmt"

	"github.com/injoyai/tdx/protocol"
	"github.com/yinhm/alphalake/internal/domain"
)

var aShareExchanges = []protocol.Exchange{
	protocol.ExchangeSH,
	protocol.ExchangeSZ,
	protocol.ExchangeBJ,
}

type codeListClient interface {
	GetCodeAll(exchange protocol.Exchange) (*protocol.CodeResp, error)
}

// Instruments returns the current TDX security master as provider-neutral
// observations. TDX symbols remain identifiers; they never become canonical IDs.
func (c *Client) Instruments(ctx context.Context) ([]domain.InstrumentObservation, error) {
	if c == nil || c.raw == nil {
		return nil, fmt.Errorf("TDX client is not initialized")
	}
	return listInstruments(ctx, c.raw, aShareExchanges)
}

func listInstruments(ctx context.Context, c codeListClient, exchanges []protocol.Exchange) ([]domain.InstrumentObservation, error) {
	var observations []domain.InstrumentObservation
	for _, exchange := range exchanges {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		resp, err := c.GetCodeAll(exchange)
		if err != nil {
			return nil, fmt.Errorf("fetch TDX code list for %s: %w", exchange.String(), err)
		}
		if resp == nil {
			continue
		}
		for _, item := range resp.List {
			if err := ctx.Err(); err != nil {
				return nil, err
			}
			if item == nil || item.Code == "" {
				continue
			}

			symbol := exchange.String() + item.Code
			key, err := NormalizeSymbol(symbol)
			if err != nil {
				return nil, fmt.Errorf("normalize TDX instrument %q: %w", symbol, err)
			}
			observations = append(observations, domain.InstrumentObservation{
				Instrument: domain.InstrumentRef{
					Type:        classifyInstrument(symbol),
					ExchangeMIC: key.ExchangeMIC,
					Currency:    "CNY",
					Name:        item.Name,
				},
				Identifier: domain.Identifier{
					Provider: Provider,
					Type:     "symbol",
					Value:    key.ProviderSymbol,
				},
			})
		}
	}
	return observations, nil
}

func classifyInstrument(symbol string) domain.InstrumentType {
	switch {
	case protocol.IsETF(symbol):
		return domain.InstrumentETF
	case protocol.IsConvertibleBond(symbol):
		return domain.InstrumentBond
	case protocol.IsStock(symbol):
		return domain.InstrumentEquity
	case protocol.IsIndex(symbol):
		return domain.InstrumentIndex
	default:
		return domain.InstrumentUnknown
	}
}
