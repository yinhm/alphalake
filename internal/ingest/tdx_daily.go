package ingest

import (
	"context"
	"database/sql"
	"fmt"

	"github.com/yinhm/alphalake/internal/domain"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

// TDXDailySource is the narrow source contract needed by the first market-data
// vertical slice. Keeping it here makes orchestration testable without a live TDX server.
type TDXDailySource interface {
	Instruments(context.Context) ([]domain.InstrumentObservation, error)
	StockDailyBars(context.Context, int64, string) ([]domain.DailyBar, error)
}

// SyncTDXDaily resolves one TDX symbol into the canonical instrument master and
// refreshes its complete unadjusted daily-bar history.
func SyncTDXDaily(ctx context.Context, db *sql.DB, source TDXDailySource, symbol string) (int, error) {
	observations, err := source.Instruments(ctx)
	if err != nil {
		return 0, fmt.Errorf("list TDX instruments: %w", err)
	}

	var observation *domain.InstrumentObservation
	for i := range observations {
		if observations[i].Identifier.Provider == "tdx" && observations[i].Identifier.Value == symbol {
			observation = &observations[i]
			break
		}
	}
	if observation == nil {
		return 0, fmt.Errorf("TDX instrument %q not found", symbol)
	}

	instrumentID, err := duckstore.UpsertInstrument(ctx, db, observation.Instrument, observation.Identifier)
	if err != nil {
		return 0, fmt.Errorf("upsert canonical instrument %q: %w", symbol, err)
	}

	bars, err := source.StockDailyBars(ctx, instrumentID, symbol)
	if err != nil {
		return 0, fmt.Errorf("fetch daily bars %q: %w", symbol, err)
	}
	if err := duckstore.UpsertDailyBars(ctx, db, bars); err != nil {
		return 0, fmt.Errorf("store daily bars %q: %w", symbol, err)
	}
	return len(bars), nil
}
