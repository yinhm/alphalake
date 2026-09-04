package tdx

import (
	"context"
	"fmt"
	"time"

	"github.com/injoyai/tdx/protocol"
	"github.com/yinhm/alphalake/internal/domain"
)

var aShareExchanges = []protocol.Exchange{
	protocol.ExchangeSH,
	protocol.ExchangeSZ,
	protocol.ExchangeBJ,
}

var tdxMarketZone = time.FixedZone("Asia/Shanghai", 8*60*60)

type codeListClient interface {
	GetCodeAll(exchange protocol.Exchange) (*protocol.CodeResp, error)
}

// InstrumentSnapshot returns a complete current TDX security-master snapshot.
// Every configured exchange partition must return a non-empty response; a
// partial/nil partition is an error rather than an apparently smaller complete
// universe that could incorrectly close thousands of identifiers downstream.
func (c *Client) InstrumentSnapshot(ctx context.Context) (domain.InstrumentMasterSnapshot, error) {
	if c == nil || c.raw == nil {
		return domain.InstrumentMasterSnapshot{}, fmt.Errorf("TDX client is not initialized")
	}
	return loadInstrumentSnapshot(ctx, c.raw, aShareExchanges, time.Now())
}

// Instruments remains as the narrow compatibility view used by tests and
// callers that do not need snapshot lifecycle metadata. Production ingestion
// detects InstrumentSnapshot and applies temporal security-master diffing.
func (c *Client) Instruments(ctx context.Context) ([]domain.InstrumentObservation, error) {
	snapshot, err := c.InstrumentSnapshot(ctx)
	if err != nil {
		return nil, err
	}
	return snapshot.Observations, nil
}

func listInstruments(ctx context.Context, c codeListClient, exchanges []protocol.Exchange) ([]domain.InstrumentObservation, error) {
	snapshot, err := loadInstrumentSnapshot(ctx, c, exchanges, time.Now())
	if err != nil {
		return nil, err
	}
	return snapshot.Observations, nil
}

func loadInstrumentSnapshot(ctx context.Context, c codeListClient, exchanges []protocol.Exchange, observedAt time.Time) (domain.InstrumentMasterSnapshot, error) {
	if observedAt.IsZero() {
		return domain.InstrumentMasterSnapshot{}, fmt.Errorf("TDX security-master observation time is zero")
	}
	local := observedAt.In(tdxMarketZone)
	asOf := time.Date(local.Year(), local.Month(), local.Day(), 0, 0, 0, 0, time.UTC)
	snapshot := domain.InstrumentMasterSnapshot{Source: Provider, AsOfDate: asOf, Complete: true}

	for _, exchange := range exchanges {
		if err := ctx.Err(); err != nil {
			return domain.InstrumentMasterSnapshot{}, err
		}
		resp, err := c.GetCodeAll(exchange)
		if err != nil {
			return domain.InstrumentMasterSnapshot{}, fmt.Errorf("fetch TDX code list for %s: %w", exchange.String(), err)
		}
		if resp == nil || len(resp.List) == 0 {
			return domain.InstrumentMasterSnapshot{}, fmt.Errorf("TDX code list for %s is empty; refuse incomplete security-master snapshot", exchange.String())
		}
		for _, item := range resp.List {
			if err := ctx.Err(); err != nil {
				return domain.InstrumentMasterSnapshot{}, err
			}
			if item == nil || item.Code == "" {
				continue
			}

			symbol := exchange.String() + item.Code
			key, err := NormalizeSymbol(symbol)
			if err != nil {
				return domain.InstrumentMasterSnapshot{}, fmt.Errorf("normalize TDX instrument %q: %w", symbol, err)
			}
			snapshot.Observations = append(snapshot.Observations, domain.InstrumentObservation{
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
	if len(snapshot.Observations) == 0 {
		return domain.InstrumentMasterSnapshot{}, fmt.Errorf("TDX security-master snapshot contains no instruments")
	}
	return snapshot, nil
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
