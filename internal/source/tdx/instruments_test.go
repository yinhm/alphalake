package tdx

import (
	"context"
	"testing"
	"time"

	"github.com/injoyai/tdx/protocol"
	"github.com/yinhm/alphalake/internal/domain"
)

type fakeCodeListClient struct {
	responses map[protocol.Exchange]*protocol.CodeResp
}

func (f fakeCodeListClient) GetCodeAll(exchange protocol.Exchange) (*protocol.CodeResp, error) {
	return f.responses[exchange], nil
}

func TestListInstrumentsNormalizesTDXCodeList(t *testing.T) {
	fake := fakeCodeListClient{responses: map[protocol.Exchange]*protocol.CodeResp{
		protocol.ExchangeSH: {List: []*protocol.Code{
			{Code: "600519", Name: "贵州茅台"},
			{Code: "510300", Name: "沪深300ETF"},
			{Code: "113001", Name: "转债样例"},
			{Code: "000001", Name: "上证指数"},
		}},
	}}

	observations, err := listInstruments(context.Background(), fake, []protocol.Exchange{protocol.ExchangeSH})
	if err != nil {
		t.Fatalf("listInstruments() error = %v", err)
	}
	if len(observations) != 4 {
		t.Fatalf("len(observations) = %d, want 4", len(observations))
	}

	wantTypes := []domain.InstrumentType{
		domain.InstrumentEquity,
		domain.InstrumentETF,
		domain.InstrumentBond,
		domain.InstrumentIndex,
	}
	for i, observation := range observations {
		if observation.Instrument.Type != wantTypes[i] {
			t.Fatalf("observation %d type = %q, want %q", i, observation.Instrument.Type, wantTypes[i])
		}
		if observation.Instrument.ExchangeMIC != "XSHG" || observation.Instrument.Currency != "CNY" {
			t.Fatalf("observation %d market = %q/%q", i, observation.Instrument.ExchangeMIC, observation.Instrument.Currency)
		}
		if observation.Identifier.Provider != Provider || observation.Identifier.Type != "symbol" {
			t.Fatalf("observation %d identifier = %#v", i, observation.Identifier)
		}
	}
	if observations[0].Identifier.Value != "sh600519" {
		t.Fatalf("first identifier = %q, want sh600519", observations[0].Identifier.Value)
	}
}

func TestInstrumentSnapshotUsesChinaCalendarDate(t *testing.T) {
	fake := fakeCodeListClient{responses: map[protocol.Exchange]*protocol.CodeResp{
		protocol.ExchangeSH: {List: []*protocol.Code{{Code: "600519", Name: "贵州茅台"}}},
	}}
	observedAt := time.Date(2026, 9, 3, 16, 30, 0, 0, time.UTC) // 2026-09-04 in China.
	snapshot, err := loadInstrumentSnapshot(context.Background(), fake, []protocol.Exchange{protocol.ExchangeSH}, observedAt)
	if err != nil {
		t.Fatal(err)
	}
	want := time.Date(2026, 9, 4, 0, 0, 0, 0, time.UTC)
	if !snapshot.Complete || snapshot.Source != Provider || !snapshot.AsOfDate.Equal(want) {
		t.Fatalf("snapshot metadata = %#v, want complete TDX snapshot at %v", snapshot, want)
	}
}

func TestInstrumentSnapshotRejectsEmptyPartition(t *testing.T) {
	fake := fakeCodeListClient{responses: map[protocol.Exchange]*protocol.CodeResp{
		protocol.ExchangeSH: {List: []*protocol.Code{{Code: "600519", Name: "贵州茅台"}}},
		protocol.ExchangeSZ: {List: nil},
	}}
	_, err := loadInstrumentSnapshot(
		context.Background(), fake,
		[]protocol.Exchange{protocol.ExchangeSH, protocol.ExchangeSZ},
		time.Date(2026, 9, 4, 12, 0, 0, 0, time.UTC),
	)
	if err == nil {
		t.Fatal("expected incomplete security-master error")
	}
}
