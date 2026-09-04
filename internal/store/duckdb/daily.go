package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"

	"github.com/yinhm/alphalake/internal/domain"
)

// UpsertDailyBars writes canonical unadjusted daily bars. The canonical key is
// (instrument_id, trade_date, source), so re-ingestion refreshes a provider's
// observation without duplicating it.
func UpsertDailyBars(ctx context.Context, db *sql.DB, bars []domain.DailyBar) error {
	if db == nil {
		return errors.New("duckdb is nil")
	}
	if len(bars) == 0 {
		return nil
	}

	for i, bar := range bars {
		if bar.InstrumentID <= 0 {
			return fmt.Errorf("bar %d: instrument ID must be positive", i)
		}
		if bar.TradeDate.IsZero() {
			return fmt.Errorf("bar %d: trade date is required", i)
		}
		if strings.TrimSpace(bar.Source) == "" {
			return fmt.Errorf("bar %d: source is required", i)
		}
	}

	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("begin daily-bar upsert: %w", err)
	}
	defer tx.Rollback()

	stmt, err := tx.PrepareContext(ctx, `
		INSERT INTO market.ohlcv_daily (
			instrument_id, trade_date,
			open, high, low, close,
			volume, amount, up_count, down_count, source
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT (instrument_id, trade_date, source) DO UPDATE SET
			open = excluded.open,
			high = excluded.high,
			low = excluded.low,
			close = excluded.close,
			volume = excluded.volume,
			amount = excluded.amount,
			up_count = excluded.up_count,
			down_count = excluded.down_count,
			ingested_at = current_timestamp
	`)
	if err != nil {
		return fmt.Errorf("prepare daily-bar upsert: %w", err)
	}
	defer stmt.Close()

	for _, bar := range bars {
		if _, err := stmt.ExecContext(ctx,
			bar.InstrumentID,
			bar.TradeDate,
			bar.Open,
			bar.High,
			bar.Low,
			bar.Close,
			bar.Volume,
			bar.Amount,
			bar.UpCount,
			bar.DownCount,
			bar.Source,
		); err != nil {
			return fmt.Errorf("upsert daily bar for instrument %d on %s: %w", bar.InstrumentID, bar.TradeDate.Format("2006-01-02"), err)
		}
	}

	if err := tx.Commit(); err != nil {
		return fmt.Errorf("commit daily-bar upsert: %w", err)
	}
	return nil
}
