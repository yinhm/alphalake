package tdx

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/injoyai/tdx/protocol"
	"github.com/yinhm/alphalake/internal/domain"
)

type fakeCodeListClient struct {
	responses map[protocol.Exchange]*protocol.CodeResp
	errors    map[protocol.Exchange]error
}

func (f fakeCodeListClient) GetCodeAll(exchange protocol.Exchange) (*protocol.CodeResp, error) {
	if err := f.errors[exchange]; err != nil {
		return nil, err
	}
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
	if len(snapshot.Partitions) != 1 || !snapshot.Partitions[0].Complete || snapshot.Partitions[0].ExchangeMIC != "XSHG" {
		t.Fatalf("partitions=%#v", snapshot.Partitions)
	}
}

func TestInstrumentSnapshotKeepsHealthyPartitionWhenAnotherIsEmpty(t *testing.T) {
	fake := fakeCodeListClient{responses: map[protocol.Exchange]*protocol.CodeResp{
		protocol.ExchangeSH: {List: []*protocol.Code{{Code: "600519", Name: "贵州茅台"}}},
		protocol.ExchangeBJ: {List: nil},
	}}
	snapshot, err := loadInstrumentSnapshot(
		context.Background(), fake,
		[]protocol.Exchange{protocol.ExchangeSH, protocol.ExchangeBJ},
		time.Date(2026, 9, 4, 12, 0, 0, 0, time.UTC),
	)
	if err != nil {
		t.Fatalf("partial snapshot error=%v", err)
	}
	if snapshot.Complete || len(snapshot.Observations) != 1 || len(snapshot.Partitions) != 2 {
		t.Fatalf("snapshot=%#v", snapshot)
	}
	if !snapshot.Partitions[0].Complete || snapshot.Partitions[1].Complete || snapshot.Partitions[1].Error == "" {
		t.Fatalf("partitions=%#v", snapshot.Partitions)
	}
}

func TestInstrumentSnapshotKeepsHealthyPartitionWhenAnotherFetchFails(t *testing.T) {
	fake := fakeCodeListClient{
		responses: map[protocol.Exchange]*protocol.CodeResp{
			protocol.ExchangeSH: {List: []*protocol.Code{{Code: "600519", Name: "贵州茅台"}}},
		},
		errors: map[protocol.Exchange]error{protocol.ExchangeBJ: errors.New("server unsupported")},
	}
	snapshot, err := loadInstrumentSnapshot(context.Background(), fake, []protocol.Exchange{protocol.ExchangeSH, protocol.ExchangeBJ}, time.Now())
	if err != nil || len(snapshot.Observations) != 1 || snapshot.Partitions[1].Complete {
		t.Fatalf("snapshot=%#v err=%v", snapshot, err)
	}
}

func TestInstrumentSnapshotFailsOnlyWhenNoPartitionUsable(t *testing.T) {
	fake := fakeCodeListClient{responses: map[protocol.Exchange]*protocol.CodeResp{
		protocol.ExchangeSH: {List: nil},
		protocol.ExchangeBJ: {List: nil},
	}}
	if _, err := loadInstrumentSnapshot(context.Background(), fake, []protocol.Exchange{protocol.ExchangeSH, protocol.ExchangeBJ}, time.Now()); err == nil {
		t.Fatal("expected error when no security-master partition is usable")
	}
}
