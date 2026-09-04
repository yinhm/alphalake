package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
)

// AdjustmentInputSignature summarizes the canonical content used by one
// adjustment calculation. It deliberately excludes ingestion lineage: replaying
// an identical boundary bar or identical full GBBQ snapshot must not dirty
// derived data merely because ingest_run_id/ingested_at/sequence IDs changed.
// Historical content corrections still change the signature even when the
// latest trade/action date is unchanged.
func AdjustmentInputSignature(ctx context.Context, db *sql.DB, instrumentID int64, source string) (signature string, hasDaily bool, err error) {
	if db == nil {
		return "", false, errors.New("duckdb is nil")
	}
	if instrumentID <= 0 {
		return "", false, errors.New("instrument ID must be positive")
	}
	source = strings.TrimSpace(source)
	if source == "" {
		return "", false, errors.New("source is required")
	}

	var dailyCount int64
	var dailyHash sql.NullString
	if err := db.QueryRowContext(ctx, `
		SELECT count(*),
		       md5(string_agg(
		           CAST(trade_date AS VARCHAR) || '|' ||
		           COALESCE(CAST(open AS VARCHAR), '<null>') || '|' ||
		           COALESCE(CAST(high AS VARCHAR), '<null>') || '|' ||
		           COALESCE(CAST(low AS VARCHAR), '<null>') || '|' ||
		           COALESCE(CAST(close AS VARCHAR), '<null>') || '|' ||
		           COALESCE(CAST(volume AS VARCHAR), '<null>') || '|' ||
		           COALESCE(CAST(amount AS VARCHAR), '<null>') || '|' ||
		           COALESCE(CAST(up_count AS VARCHAR), '<null>') || '|' ||
		           COALESCE(CAST(down_count AS VARCHAR), '<null>'),
		           ';' ORDER BY trade_date
		       ))
		FROM market.ohlcv_daily
		WHERE instrument_id=? AND source=?
	`, instrumentID, source).Scan(&dailyCount, &dailyHash); err != nil {
		return "", false, fmt.Errorf("query adjustment daily content signature: %w", err)
	}
	if dailyCount == 0 {
		return "", false, nil
	}

	var actionCount int64
	var actionHash sql.NullString
	if err := db.QueryRowContext(ctx, `
		SELECT count(*),
		       md5(string_agg(
		           COALESCE(
		               source_record_id,
		               CAST(action_date AS VARCHAR) || '|' ||
		               COALESCE(CAST(source_category AS VARCHAR), '<null>') || '|' ||
		               action_type || '|' ||
		               COALESCE(CAST(raw_c1 AS VARCHAR), '<null>') || '|' ||
		               COALESCE(CAST(raw_c2 AS VARCHAR), '<null>') || '|' ||
		               COALESCE(CAST(raw_c3 AS VARCHAR), '<null>') || '|' ||
		               COALESCE(CAST(raw_c4 AS VARCHAR), '<null>')
		           ),
		           ';' ORDER BY action_date, source_category, source_record_id
		       ))
		FROM market.corporate_action
		WHERE instrument_id=? AND source=?
	`, instrumentID, source).Scan(&actionCount, &actionHash); err != nil {
		return "", false, fmt.Errorf("query adjustment action content signature: %w", err)
	}

	return fmt.Sprintf(
		"content-v1:daily:%d:%s:actions:%d:%s",
		dailyCount, nullString(dailyHash), actionCount, nullString(actionHash),
	), true, nil
}

func DerivedStateSignature(ctx context.Context, db *sql.DB, dataset string, instrumentID int64, source, method string) (string, bool, error) {
	if db == nil {
		return "", false, errors.New("duckdb is nil")
	}
	dataset = strings.TrimSpace(dataset)
	source = strings.TrimSpace(source)
	method = strings.TrimSpace(method)
	if dataset == "" || source == "" || method == "" || instrumentID <= 0 {
		return "", false, errors.New("dataset, instrument ID, source, and method are required")
	}
	var signature string
	err := db.QueryRowContext(ctx, `
		SELECT input_signature
		FROM meta.derived_state
		WHERE dataset=? AND instrument_id=? AND source=? AND method=?
	`, dataset, instrumentID, source, method).Scan(&signature)
	if errors.Is(err, sql.ErrNoRows) {
		return "", false, nil
	}
	if err != nil {
		return "", false, fmt.Errorf("query derived state: %w", err)
	}
	return signature, true, nil
}

func nullString(v sql.NullString) string {
	if !v.Valid {
		return "-"
	}
	return v.String
}
