package tdx

import (
	"context"
	"testing"
	"time"

	"github.com/injoyai/tdx/protocol"
)

type fakeGBBQClient struct {
	code string
	resp *protocol.GbbqResp
}

func (f *fakeGBBQClient) GetGbbq(code string) (*protocol.GbbqResp, error) {
	f.code = code
	return f.resp, nil
}

func TestFetchCorporateActionsNormalizesGBBQ(t *testing.T) {
	day := time.Date(2026, 6, 30, 15, 0, 0, 0, time.Local)
	fake := &fakeGBBQClient{resp: &protocol.GbbqResp{List: []*protocol.Gbbq{
		{Code: "sh600519", Time: day, Category: 1, C1: 10.5, C2: 0, C3: 2, C4: 0},
		{Code: "sh600519", Time: day.AddDate(0, 1, 0), Category: 5, C3: 123456789, C4: 200000000},
	}}}

	observations, err := fetchCorporateActions(context.Background(), fake, "SH600519")
	if err != nil {
		t.Fatalf("fetchCorporateActions() error = %v", err)
	}
	if fake.code != "sh600519" {
		t.Fatalf("GetGbbq code = %q, want sh600519", fake.code)
	}
	if len(observations) != 2 {
		t.Fatalf("len(observations) = %d, want 2", len(observations))
	}
	if observations[0].Action.ActionType != "distribution" || observations[0].Action.CashDividendPer10 != 10.5 {
		t.Fatalf("distribution = %#v", observations[0].Action)
	}
	if observations[0].Identifier.Value != "sh600519" {
		t.Fatalf("identifier = %#v", observations[0].Identifier)
	}
	if observations[1].ShareCapital == nil || observations[1].ShareCapital.TotalShares != 200000000 {
		t.Fatalf("share capital = %#v", observations[1].ShareCapital)
	}
}
