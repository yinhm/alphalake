package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"

	"github.com/yinhm/alphalake/internal/domain"
)

func LoadDailyBars(ctx context.Context, db *sql.DB, instrumentID int64, source string) ([]domain.DailyBar, error) {
	if db == nil {
		return nil, errors.New("duckdb is nil")
	}
	if instrumentID <= 0 {
		return nil, errors.New("instrument ID must be positive")
	}
	source = strings.TrimSpace(source)
	if source == "" {
		return nil, errors.New("source is required")
	}

	rows, err := db.QueryContext(ctx, `
		SELECT instrument_id, trade_date, open, high, low, close,
		       volume, amount, up_count, down_count, source
		FROM market.ohlcv_daily
		WHERE instrument_id = ? AND source = ?
		ORDER BY trade_date
	`, instrumentID, source)
	if err != nil {
		return nil, fmt.Errorf("query daily bars: %w", err)
	}
	defer rows.Close()

	var out []domain.DailyBar
	for rows.Next() {
		var bar domain.DailyBar
		if err := rows.Scan(
			&bar.InstrumentID, &bar.TradeDate, &bar.Open, &bar.High, &bar.Low, &bar.Close,
			&bar.Volume, &bar.Amount, &bar.UpCount, &bar.DownCount, &bar.Source,
		); err != nil {
			return nil, fmt.Errorf("scan daily bar: %w", err)
		}
		out = append(out, bar)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate daily bars: %w", err)
	}
	return out, nil
}

func LoadCorporateActions(ctx context.Context, db *sql.DB, instrumentID int64, source string) ([]domain.CorporateAction, error) {
	if db == nil {
		return nil, errors.New("duckdb is nil")
	}
	if instrumentID <= 0 {
		return nil, errors.New("instrument ID must be positive")
	}
	source = strings.TrimSpace(source)
	if source == "" {
		return nil, errors.New("source is required")
	}

	rows, err := db.QueryContext(ctx, `
		SELECT instrument_id, action_date, action_type, source_category,
		       source, source_record_id,
		       COALESCE(cash_dividend_per_10, 0), COALESCE(rights_price, 0),
		       COALESCE(bonus_or_split_per_10, 0), COALESCE(rights_per_10, 0),
		       COALESCE(scale_factor, 0),
		       raw_c1, raw_c2, raw_c3, raw_c4
		FROM market.corporate_action
		WHERE instrument_id = ? AND source = ?
		ORDER BY action_date, source_record_id
	`, instrumentID, source)
	if err != nil {
		return nil, fmt.Errorf("query corporate actions: %w", err)
	}
	defer rows.Close()

	var out []domain.CorporateAction
	for rows.Next() {
		var action domain.CorporateAction
		if err := rows.Scan(
			&action.InstrumentID, &action.ActionDate, &action.ActionType, &action.SourceCategory,
			&action.Source, &action.SourceRecordID,
			&action.CashDividendPer10, &action.RightsPrice,
			&action.BonusOrSplitPer10, &action.RightsPer10,
			&action.ScaleFactor,
			&action.RawC1, &action.RawC2, &action.RawC3, &action.RawC4,
		); err != nil {
			return nil, fmt.Errorf("scan corporate action: %w", err)
		}
		out = append(out, action)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate corporate actions: %w", err)
	}
	return out, nil
}

// ReplaceAdjustmentSegmentsForRun atomically replaces one derived adjustment
// method/source snapshot for one instrument.
func ReplaceAdjustmentSegmentsForRun(ctx context.Context, db *sql.DB, ingestRunID, instrumentID int64, method, source string, segments []domain.AdjustmentSegment) error {
	if db == nil {
		return errors.New("duckdb is nil")
	}
	if ingestRunID <= 0 {
		return errors.New("ingest run ID must be positive")
	}
	if instrumentID <= 0 {
		return errors.New("instrument ID must be positive")
	}
	method = strings.TrimSpace(method)
	source = strings.TrimSpace(source)
	if method == "" || source == "" {
		return errors.New("method and source are required")
	}
	for i, segment := range segments {
		if segment.InstrumentID != instrumentID || segment.Method != method || segment.Source != source {
			return fmt.Errorf("segment %d identity does not match snapshot", i)
		}
		if segment.EffectiveFrom.IsZero() {
			return fmt.Errorf("segment %d effective_from is required", i)
		}
		if segment.EffectiveTo != nil && segment.EffectiveTo.Before(segment.EffectiveFrom) {
			return fmt.Errorf("segment %d effective_to precedes effective_from", i)
		}
	}

	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("begin adjustment snapshot replace: %w", err)
	}
	defer tx.Rollback()

	if _, err := tx.ExecContext(ctx, `
		DELETE FROM market.adjustment_segment
		WHERE instrument_id = ? AND method = ? AND source = ?
	`, instrumentID, method, source); err != nil {
		return fmt.Errorf("delete prior adjustment segments: %w", err)
	}

	for _, segment := range segments {
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO market.adjustment_segment (
				instrument_id, effective_from, effective_to,
				qfq_mul, qfq_add, hfq_mul, hfq_add,
				method, source, ingest_run_id
			) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		`,
			segment.InstrumentID, segment.EffectiveFrom, segment.EffectiveTo,
			segment.QFQMul, segment.QFQAdd, segment.HFQMul, segment.HFQAdd,
			segment.Method, segment.Source, ingestRunID,
		); err != nil {
			return fmt.Errorf("insert adjustment segment from %s: %w", segment.EffectiveFrom.Format("2006-01-02"), err)
		}
	}

	if err := tx.Commit(); err != nil {
		return fmt.Errorf("commit adjustment snapshot replace: %w", err)
	}
	return nil
}
