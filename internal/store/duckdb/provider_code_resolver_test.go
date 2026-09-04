package duckdb

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

func TestResolveProviderCodesAtUsesTemporalIdentifiersNotCurrentCodeRanges(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "provider-code.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	from := time.Date(2000, 1, 1, 0, 0, 0, 0, time.UTC)
	for _, tc := range []struct {
		symbol string
		mic    string
	}{
		{symbol: "sh900901", mic: "XSHG"}, // B-share style code
		{symbol: "sz200002", mic: "XSHE"}, // B-share style code
		{symbol: "bj430001", mic: "XBSE"}, // legacy Beijing/NEEQ-style code
	} {
		validFrom := from
		if _, err := UpsertInstrument(ctx, db,
			domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: tc.mic, Currency: "CNY", Name: tc.symbol},
			domain.Identifier{Provider: "tdx", Type: "symbol", Value: tc.symbol, ValidFrom: &validFrom},
		); err != nil {
			t.Fatal(err)
		}
	}
	codes := []string{"900901", "200002", "430001", "999999"}
	resolved, err := ResolveProviderCodesAt(ctx, db, "tdx", codes, time.Date(2005, 6, 30, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 3; i++ {
		if !resolved[i].Resolved() {
			t.Fatalf("code %s unresolved: %#v", codes[i], resolved[i])
		}
	}
	if resolved[3].Resolved() || len(resolved[3].Candidates) != 0 {
		t.Fatalf("missing code unexpectedly resolved: %#v", resolved[3])
	}
}

func TestResolveProviderCodesAtExcludesIndexCollision(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "provider-code-index-collision.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	if _, err := UpsertInstrument(ctx, db,
		domain.InstrumentRef{Type: domain.InstrumentIndex, ExchangeMIC: "XSHG", Currency: "CNY", Name: "上证指数"},
		domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh000001"},
	); err != nil {
		t.Fatal(err)
	}
	bankID, err := UpsertInstrument(ctx, db,
		domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHE", Currency: "CNY", Name: "平安银行"},
		domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sz000001"},
	)
	if err != nil {
		t.Fatal(err)
	}

	resolved, err := ResolveProviderCodesAt(ctx, db, "tdx", []string{"000001"}, time.Now().UTC())
	if err != nil {
		t.Fatal(err)
	}
	if !resolved[0].Resolved() {
		t.Fatalf("000001 unresolved: %#v", resolved[0])
	}
	if resolved[0].InstrumentID != bankID || resolved[0].IdentifierValue != "sz000001" {
		t.Fatalf("000001 resolved to %#v, want sz000001 instrument %d", resolved[0], bankID)
	}
	if len(resolved[0].Candidates) != 1 || resolved[0].Candidates[0] != "sz000001" {
		t.Fatalf("000001 candidates=%v, want only sz000001", resolved[0].Candidates)
	}
}

func TestResolveProviderCodesAtLeavesCrossMarketEquityAmbiguityUnresolved(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "provider-code-ambiguous.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	for _, tc := range []struct {
		symbol string
		mic    string
	}{
		{symbol: "sh123456", mic: "XSHG"},
		{symbol: "sz123456", mic: "XSHE"},
	} {
		if _, err := UpsertInstrument(ctx, db,
			domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: tc.mic, Currency: "CNY", Name: tc.symbol},
			domain.Identifier{Provider: "tdx", Type: "symbol", Value: tc.symbol},
		); err != nil {
			t.Fatal(err)
		}
	}
	resolved, err := ResolveProviderCodesAt(ctx, db, "tdx", []string{"123456"}, time.Now().UTC())
	if err != nil {
		t.Fatal(err)
	}
	if resolved[0].Resolved() || len(resolved[0].Candidates) != 2 {
		t.Fatalf("ambiguous code=%#v, want unresolved with two equity candidates", resolved[0])
	}
}
