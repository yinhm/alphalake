package ingest

import (
	"context"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

type fakeTDXDailySource struct {
	observations []domain.InstrumentObservation
	bars         []domain.DailyBar
	barCalls     int
}

func (f fakeTDXDailySource) Instruments(context.Context) ([]domain.InstrumentObservation, error) {
	return f.observations, nil
}

func (f fakeTDXDailySource) StockDailyBars(_ context.Context, instrumentID int64, _ string) ([]domain.DailyBar, error) {
	bars := make([]domain.DailyBar, len(f.bars))
	copy(bars, f.bars)
	for i := range bars {
		bars[i].InstrumentID = instrumentID
	}
	return bars, nil
}

func TestSyncTDXDailyPersistsCanonicalInstrumentAndBars(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "alphalake.duckdb"))
	if err != nil {
		t.Fatalf("OpenAndMigrate() error = %v", err)
	}
	defer db.Close()

	source := fakeTDXDailySource{
		observations: []domain.InstrumentObservation{{
			Instrument: domain.InstrumentRef{
				Type:        domain.InstrumentEquity,
				ExchangeMIC: "XSHG",
				Currency:    "CNY",
				Name:        "贵州茅台",
			},
			Identifier: domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600519"},
		}},
		bars: []domain.DailyBar{{
			TradeDate: time.Date(2026, 9, 3, 0, 0, 0, 0, time.UTC),
			Open:      1500,
			High:      1520,
			Low:       1490,
			Close:     1510,
			Volume:    1234567,
			Amount:    1.86e9,
			Source:    "tdx",
		}},
	}

	n, err := SyncTDXDaily(ctx, db, source, "sh600519")
	if err != nil {
		t.Fatalf("SyncTDXDaily() error = %v", err)
	}
	if n != 1 {
		t.Fatalf("SyncTDXDaily() rows = %d, want 1", n)
	}

	var instrumentID int64
	if err := db.QueryRowContext(ctx, `
		SELECT instrument_id
		FROM ref.instrument_identifier
		WHERE provider='tdx' AND identifier_type='symbol' AND identifier_value='sh600519'
	`).Scan(&instrumentID); err != nil {
		t.Fatalf("query instrument: %v", err)
	}

	var count int
	if err := db.QueryRowContext(ctx, `
		SELECT count(*) FROM market.ohlcv_daily WHERE instrument_id=? AND source='tdx'
	`, instrumentID).Scan(&count); err != nil {
		t.Fatalf("query bars: %v", err)
	}
	if count != 1 {
		t.Fatalf("bar count = %d, want 1", count)
	}
}

func TestSyncTDXDailyRejectsUnsupportedInstrumentType(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "unsupported.duckdb"))
	if err != nil {
		t.Fatalf("OpenAndMigrate() error = %v", err)
	}
	defer db.Close()

	source := fakeTDXDailySource{observations: []domain.InstrumentObservation{{
		Instrument: domain.InstrumentRef{
			Type: domain.InstrumentIndex, ExchangeMIC: "XSHG", Currency: "CNY", Name: "上证指数",
		},
		Identifier: domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh000001"},
	}}}

	_, err = SyncTDXDaily(ctx, db, source, "sh000001")
	if err == nil || !strings.Contains(err.Error(), "not supported") {
		t.Fatalf("SyncTDXDaily() error = %v, want unsupported type error", err)
	}

	var count int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM market.ohlcv_daily`).Scan(&count); err != nil {
		t.Fatalf("count bars: %v", err)
	}
	if count != 0 {
		t.Fatalf("bar count = %d, want 0", count)
	}
}
