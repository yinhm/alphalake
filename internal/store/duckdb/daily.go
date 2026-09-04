package duckdb

import (
	"context"
	"database/sql"
	"database/sql/driver"
	"errors"
	"fmt"
	"strings"
	"time"

	duckdbgo "github.com/duckdb/duckdb-go/v2"
	"github.com/yinhm/alphalake/internal/domain"
)

const dailyStageTable = "_alphalake_daily_stage"

// LatestDailyDate returns the newest stored observation for one instrument and
// source. It acts as the resumable boundary for incremental market ingestion.
func LatestDailyDate(ctx context.Context, db *sql.DB, instrumentID int64, source string) (time.Time, bool, error) {
	if db == nil {
		return time.Time{}, false, errors.New("duckdb is nil")
	}
	if instrumentID <= 0 {
		return time.Time{}, false, errors.New("instrument ID must be positive")
	}
	if strings.TrimSpace(source) == "" {
		return time.Time{}, false, errors.New("source is required")
	}

	var latest sql.NullTime
	if err := db.QueryRowContext(ctx, `
		SELECT max(trade_date)
		FROM market.ohlcv_daily
		WHERE instrument_id = ? AND source = ?
	`, instrumentID, source).Scan(&latest); err != nil {
		return time.Time{}, false, fmt.Errorf("query latest daily date: %w", err)
	}
	if !latest.Valid {
		return time.Time{}, false, nil
	}
	return latest.Time, true, nil
}

// UpsertDailyBars writes canonical unadjusted daily bars without changing their
// existing ingest-run lineage when refreshing a row outside a tracked run.
func UpsertDailyBars(ctx context.Context, db *sql.DB, bars []domain.DailyBar) error {
	return upsertDailyBars(ctx, db, bars, nil)
}

// UpsertDailyBarsForRun writes canonical bars and records which ingest run most
// recently inserted/refreshed each observation.
func UpsertDailyBarsForRun(ctx context.Context, db *sql.DB, ingestRunID int64, bars []domain.DailyBar) error {
	if ingestRunID <= 0 {
		return errors.New("ingest run ID must be positive")
	}
	return upsertDailyBars(ctx, db, bars, &ingestRunID)
}

func upsertDailyBars(ctx context.Context, db *sql.DB, bars []domain.DailyBar, ingestRunID *int64) error {
	if db == nil {
		return errors.New("duckdb is nil")
	}
	if len(bars) == 0 {
		return nil
	}
	if err := validateDailyBarKeys(bars); err != nil {
		return err
	}
	return withDailyWriteTransaction(ctx, db, func(conn *sql.Conn) error {
		return mergeDailyBarsOnConn(ctx, conn, bars, ingestRunID)
	})
}

func validateDailyBarKeys(bars []domain.DailyBar) error {
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
	return nil
}

// withDailyWriteTransaction owns the dedicated connection required by DuckDB's
// Appender. Callers may compose canonical bar merge, validation evidence and
// checkpoint changes inside the same transaction. A callback error rolls back
// every effect before the connection is returned to the pool.
func withDailyWriteTransaction(ctx context.Context, db *sql.DB, fn func(*sql.Conn) error) error {
	if db == nil {
		return errors.New("duckdb is nil")
	}
	conn, err := db.Conn(ctx)
	if err != nil {
		return fmt.Errorf("acquire daily-bar connection: %w", err)
	}
	defer conn.Close()
	if _, err := conn.ExecContext(ctx, `DROP TABLE IF EXISTS temp.main.`+dailyStageTable); err != nil {
		return fmt.Errorf("cleanup daily staging table: %w", err)
	}
	if _, err := conn.ExecContext(ctx, `BEGIN TRANSACTION`); err != nil {
		return fmt.Errorf("begin daily write transaction: %w", err)
	}
	committed := false
	defer func() {
		if !committed {
			_, _ = conn.ExecContext(context.Background(), `ROLLBACK`)
		}
		_, _ = conn.ExecContext(context.Background(), `DROP TABLE IF EXISTS temp.main.`+dailyStageTable)
	}()

	if err := fn(conn); err != nil {
		return err
	}
	if _, err := conn.ExecContext(ctx, `COMMIT`); err != nil {
		return fmt.Errorf("commit daily write transaction: %w", err)
	}
	committed = true
	return nil
}

// mergeDailyBarsOnConn requires an active transaction on conn. It bulk-appends
// into a connection-local temporary table and performs one set-based upsert.
func mergeDailyBarsOnConn(ctx context.Context, conn *sql.Conn, bars []domain.DailyBar, ingestRunID *int64) error {
	if len(bars) == 0 {
		return nil
	}
	if err := validateDailyBarKeys(bars); err != nil {
		return err
	}
	if _, err := conn.ExecContext(ctx, `DROP TABLE IF EXISTS temp.main.`+dailyStageTable); err != nil {
		return fmt.Errorf("cleanup daily staging table: %w", err)
	}
	if _, err := conn.ExecContext(ctx, `
		CREATE TEMP TABLE `+dailyStageTable+` (
			instrument_id BIGINT NOT NULL,
			trade_date DATE NOT NULL,
			open DOUBLE,
			high DOUBLE,
			low DOUBLE,
			close DOUBLE,
			volume BIGINT,
			amount DOUBLE,
			up_count BIGINT,
			down_count BIGINT,
			source VARCHAR NOT NULL,
			ingest_run_id BIGINT
		)
	`); err != nil {
		return fmt.Errorf("create daily staging table: %w", err)
	}
	if err := appendDailyStage(ctx, conn, bars, ingestRunID); err != nil {
		return err
	}

	mergeSQL := `
		INSERT INTO market.ohlcv_daily (
			instrument_id, trade_date,
			open, high, low, close,
			volume, amount, up_count, down_count,
			source, ingest_run_id
		)
		SELECT
			instrument_id, trade_date,
			open, high, low, close,
			volume, amount, up_count, down_count,
			source, ingest_run_id
		FROM temp.main.` + dailyStageTable + `
		ON CONFLICT (instrument_id, trade_date, source) DO UPDATE SET
			open = excluded.open,
			high = excluded.high,
			low = excluded.low,
			close = excluded.close,
			volume = excluded.volume,
			amount = excluded.amount,
			up_count = excluded.up_count,
			down_count = excluded.down_count,
			ingested_at = now()
	`
	if ingestRunID != nil {
		mergeSQL = `
			INSERT INTO market.ohlcv_daily (
				instrument_id, trade_date,
				open, high, low, close,
				volume, amount, up_count, down_count,
				source, ingest_run_id
			)
			SELECT
				instrument_id, trade_date,
				open, high, low, close,
				volume, amount, up_count, down_count,
				source, ingest_run_id
			FROM temp.main.` + dailyStageTable + `
			ON CONFLICT (instrument_id, trade_date, source) DO UPDATE SET
				open = excluded.open,
				high = excluded.high,
				low = excluded.low,
				close = excluded.close,
				volume = excluded.volume,
				amount = excluded.amount,
				up_count = excluded.up_count,
				down_count = excluded.down_count,
				ingest_run_id = excluded.ingest_run_id,
				ingested_at = now()
		`
	}
	if _, err := conn.ExecContext(ctx, mergeSQL); err != nil {
		return fmt.Errorf("merge daily staging rows: %w", err)
	}
	if _, err := conn.ExecContext(ctx, `DROP TABLE temp.main.`+dailyStageTable); err != nil {
		return fmt.Errorf("drop daily staging table: %w", err)
	}
	return nil
}

func appendDailyStage(ctx context.Context, conn *sql.Conn, bars []domain.DailyBar, ingestRunID *int64) error {
	return conn.Raw(func(raw any) error {
		driverConn, ok := raw.(driver.Conn)
		if !ok {
			return errors.New("duckdb raw connection does not implement driver.Conn")
		}
		appender, err := duckdbgo.NewAppender(driverConn, "temp", "main", dailyStageTable)
		if err != nil {
			return fmt.Errorf("create daily staging appender: %w", err)
		}
		for _, bar := range bars {
			var runValue driver.Value
			if ingestRunID != nil {
				runValue = *ingestRunID
			}
			if err := appender.AppendRow(
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
				runValue,
			); err != nil {
				_ = appender.Clear()
				_ = appender.Close()
				return fmt.Errorf("append daily staging row for instrument %d on %s: %w", bar.InstrumentID, bar.TradeDate.Format("2006-01-02"), err)
			}
		}
		if err := appender.CloseWithCancel(ctx); err != nil {
			return fmt.Errorf("flush daily staging appender: %w", err)
		}
		return nil
	})
}
