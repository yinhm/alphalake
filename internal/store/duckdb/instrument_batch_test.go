package duckdb

import (
	"context"
	"path/filepath"
	"testing"

	"github.com/yinhm/alphalake/internal/domain"
)

func TestUpsertInstrumentsIsIdempotentAndOrdered(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "instrument-batch.duckdb"))
	if err != nil {
		t.Fatalf("OpenAndMigrate() error = %v", err)
	}
	defer db.Close()

	observations := []domain.InstrumentObservation{
		{
			Instrument: domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHG", Currency: "CNY", Name: "贵州茅台"},
			Identifier: domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600519"},
		},
		{
			Instrument: domain.InstrumentRef{Type: domain.InstrumentETF, ExchangeMIC: "XSHG", Currency: "CNY", Name: "沪深300ETF"},
			Identifier: domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh510300"},
		},
	}

	first, err := UpsertInstruments(ctx, db, observations)
	if err != nil {
		t.Fatalf("first UpsertInstruments() error = %v", err)
	}
	second, err := UpsertInstruments(ctx, db, observations)
	if err != nil {
		t.Fatalf("second UpsertInstruments() error = %v", err)
	}
	if len(first) != 2 || len(second) != 2 {
		t.Fatalf("ID lengths = %d/%d, want 2/2", len(first), len(second))
	}
	for i := range first {
		if first[i] <= 0 || first[i] != second[i] {
			t.Fatalf("IDs at %d = %d/%d, want stable positive ID", i, first[i], second[i])
		}
	}

	var count int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM ref.instrument_identifier WHERE provider='tdx'`).Scan(&count); err != nil {
		t.Fatalf("count identifiers: %v", err)
	}
	if count != 2 {
		t.Fatalf("identifier count = %d, want 2", count)
	}
}
