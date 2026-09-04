package duckdb

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"
)

// AdjustmentInputSignature summarizes the canonical input state used by one
// adjustment calculation. It intentionally uses persisted lineage/state rather
// than only the latest market date, so historical corrections also dirty the
// derived output.
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
	var dailyRun sql.NullInt64
	var dailyWritten sql.NullTime
	var latestDate sql.NullTime
	if err := db.QueryRowContext(ctx, `
		SELECT count(*), max(ingest_run_id), max(ingested_at), max(trade_date)
		FROM market.ohlcv_daily
		WHERE instrument_id=? AND source=?
	`, instrumentID, source).Scan(&dailyCount, &dailyRun, &dailyWritten, &latestDate); err != nil {
		return "", false, fmt.Errorf("query adjustment daily input state: %w", err)
	}
	if dailyCount == 0 {
		return "", false, nil
	}

	var actionCount int64
	var actionRun sql.NullInt64
	var actionRowID sql.NullInt64
	if err := db.QueryRowContext(ctx, `
		SELECT count(*), max(ingest_run_id), max(corporate_action_id)
		FROM market.corporate_action
		WHERE instrument_id=? AND source=?
	`, instrumentID, source).Scan(&actionCount, &actionRun, &actionRowID); err != nil {
		return "", false, fmt.Errorf("query adjustment action input state: %w", err)
	}

	material := fmt.Sprintf(
		"daily(count=%d,run=%d,written=%s,latest=%s);actions(count=%d,run=%d,row=%d)",
		dailyCount,
		nullInt64(dailyRun),
		nullTimeRFC3339Nano(dailyWritten),
		nullDate(latestDate),
		actionCount,
		nullInt64(actionRun),
		nullInt64(actionRowID),
	)
	sum := sha256.Sum256([]byte(material))
	return fmt.Sprintf("sha256:%x", sum[:]), true, nil
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

func nullInt64(v sql.NullInt64) int64 {
	if !v.Valid {
		return 0
	}
	return v.Int64
}

func nullTimeRFC3339Nano(v sql.NullTime) string {
	if !v.Valid {
		return "-"
	}
	return v.Time.UTC().Format(time.RFC3339Nano)
}

func nullDate(v sql.NullTime) string {
	if !v.Valid {
		return "-"
	}
	return v.Time.Format("2006-01-02")
}
