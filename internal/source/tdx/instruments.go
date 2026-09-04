package tdx

import (
	"context"
	"fmt"
	"strings"
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

// InstrumentSnapshot returns the current TDX security master with independent
// exchange-partition completeness. One failed exchange no longer prevents valid
// partitions from being refreshed; only a complete partition may authorize
// temporal closes downstream.
func (c *Client) InstrumentSnapshot(ctx context.Context) (domain.InstrumentMasterSnapshot, error) {
	if c == nil || c.raw == nil {
		return domain.InstrumentMasterSnapshot{}, fmt.Errorf("TDX client is not initialized")
	}
	return loadInstrumentSnapshot(ctx, c.raw, aShareExchanges, time.Now())
}

// Instruments remains as the narrow compatibility view used by tests and
// callers that do not need snapshot lifecycle metadata.
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
	usablePartitions := 0

	for _, exchange := range exchanges {
		if err := ctx.Err(); err != nil {
			return domain.InstrumentMasterSnapshot{}, err
		}
		partition := domain.InstrumentMasterPartition{
			Key: exchange.String(), ExchangeMIC: exchangeMIC(exchange), Complete: true,
		}
		resp, err := c.GetCodeAll(exchange)
		if err != nil {
			partition.Complete = false
			partition.Error = fmt.Sprintf("fetch TDX code list: %v", err)
			snapshot.Complete = false
			snapshot.Partitions = append(snapshot.Partitions, partition)
			continue
		}
		if resp == nil || len(resp.List) == 0 {
			partition.Complete = false
			partition.Error = "TDX code list is empty"
			snapshot.Complete = false
			snapshot.Partitions = append(snapshot.Partitions, partition)
			continue
		}

		var partitionErrors []string
		for _, item := range resp.List {
			if err := ctx.Err(); err != nil {
				return domain.InstrumentMasterSnapshot{}, err
			}
			if item == nil || strings.TrimSpace(item.Code) == "" {
				continue
			}
			symbol := exchange.String() + item.Code
			key, err := NormalizeSymbol(symbol)
			if err != nil {
				partition.Complete = false
				partitionErrors = append(partitionErrors, fmt.Sprintf("normalize %q: %v", symbol, err))
				continue
			}
			observation := domain.InstrumentObservation{
				Instrument: domain.InstrumentRef{
					Type: classifyInstrument(symbol), ExchangeMIC: key.ExchangeMIC,
					Currency: "CNY", Name: item.Name,
				},
				Identifier: domain.Identifier{Provider: Provider, Type: "symbol", Value: key.ProviderSymbol},
			}
			partition.Observations = append(partition.Observations, observation)
			snapshot.Observations = append(snapshot.Observations, observation)
		}
		if len(partition.Observations) == 0 {
			partition.Complete = false
			partitionErrors = append(partitionErrors, "partition contains no usable instruments")
		}
		if len(partitionErrors) != 0 {
			partition.Error = strings.Join(partitionErrors, "; ")
		}
		if !partition.Complete {
			snapshot.Complete = false
		}
		if len(partition.Observations) != 0 {
			usablePartitions++
		}
		snapshot.Partitions = append(snapshot.Partitions, partition)
	}
	if usablePartitions == 0 || len(snapshot.Observations) == 0 {
		return domain.InstrumentMasterSnapshot{}, fmt.Errorf("TDX security-master snapshot contains no usable partitions")
	}
	return snapshot, nil
}

func exchangeMIC(exchange protocol.Exchange) string {
	switch exchange {
	case protocol.ExchangeSH:
		return "XSHG"
	case protocol.ExchangeSZ:
		return "XSHE"
	case protocol.ExchangeBJ:
		return "XBSE"
	default:
		return ""
	}
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
