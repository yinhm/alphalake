package duckdb

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"
	"time"
)

// ExportValuationQuote exports an exact-date, observed-after-day-end A-share close.
// ASOF uses local capture + tracked run completion, never a guessed publication time.
func ExportValuationQuote(ctx context.Context, db *sql.DB, symbol string, day, asof time.Time) (map[string]any, error) {
	if db == nil || !regexp.MustCompile(`^(sh|sz|bj)[0-9]{6}$`).MatchString(symbol) || day.IsZero() || asof.IsZero() {
		return nil, errors.New("TDX symbol, date and information cutoff required")
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	out, err := exportValuationQuoteOnTx(ctx, tx, symbol, day, asof)
	if err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	return out, nil
}
func exportValuationQuoteOnTx(ctx context.Context, tx *sql.Tx, symbol string, day, asof time.Time) (map[string]any, error) {
	id, found, err := resolveInstrumentIdentifierAt(ctx, tx, "tdx", "symbol", symbol, day)
	if err != nil {
		return nil, err
	}
	if !found {
		return nil, errors.New("no unambiguous instrument identity at market date")
	}
	var mic, currency, kind string
	if err = tx.QueryRowContext(ctx, `SELECT exchange_mic,currency,instrument_type FROM core.instrument WHERE instrument_id=?`, id).Scan(&mic, &currency, &kind); err != nil {
		return nil, err
	}
	expected := map[string]string{"sh": "XSHG", "sz": "XSHE", "bj": "XBSE"}[symbol[:2]]
	if mic != expected || currency != "CNY" || kind != "equity" {
		return nil, errors.New("only reviewed mainland CNY equity quotes supported")
	}
	// Require acquisition to start AFTER the local trading date. A bar captured
	// intraday is not promoted to a completed close merely by waiting until tomorrow.
	local := time.FixedZone("China", 8*3600)
	boundary := time.Date(day.Year(), day.Month(), day.Day()+1, 0, 0, 0, 0, local)
	var payload string
	err = tx.QueryRowContext(ctx, `SELECT CAST(to_json(q) AS VARCHAR) FROM (
 SELECT o.observation_id,o.instrument_id,CAST(o.trade_date AS VARCHAR) AS trade_date,
 CAST(o.close AS VARCHAR) AS close,o.source,o.ingest_run_id,CAST(o.recorded_at AS VARCHAR) AS recorded_at,
 CAST(r.started_at AS VARCHAR) AS acquisition_started_at,CAST(r.finished_at AS VARCHAR) AS run_finished_at
 FROM market.daily_observation o JOIN meta.ingest_run r ON r.ingest_run_id=o.ingest_run_id
 WHERE o.instrument_id=? AND o.trade_date=? AND o.source='tdx' AND r.source='tdx' AND r.dataset='daily_ohlcv'
 AND r.status IN ('completed','partial') AND r.started_at>=? AND o.recorded_at<=? AND r.finished_at<=?
 ORDER BY o.recorded_at DESC,o.observation_id DESC LIMIT 1) q`, id, day.Format("2006-01-02"), boundary, asof, asof).Scan(&payload)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, errors.New("no eligible exact-date observed close; resync after date end, or review cutoff/identity")
	}
	if err != nil {
		return nil, err
	}
	var row map[string]any
	decoder := json.NewDecoder(strings.NewReader(payload))
	decoder.UseNumber()
	if err = decoder.Decode(&row); err != nil {
		return nil, err
	}
	close, ok := row["close"].(string)
	if !ok {
		return nil, errors.New("missing close")
	}
	var positive bool
	if err = tx.QueryRowContext(ctx, `SELECT CAST(? AS DECIMAL(20,6))>0`, close).Scan(&positive); err != nil || !positive {
		return nil, fmt.Errorf("invalid close: %s", close)
	}
	return map[string]any{"contract_version": "alphalake-valuation-quote-v1", "symbol": symbol, "information_as_of": asof.UTC().Format(time.RFC3339Nano),
		"currency": currency, "exchange_mic": mic, "adjustment": "unadjusted", "quote": row, "market_cap": nil,
		"market_cap_status": "missing_reviewed_share_class_outstanding",
		"boundaries":        []string{"parsed source observation; original TDX protocol bytes not archived", "no pre-upgrade observation history backfilled", "exact market date; no automatic fallback", "current instrument metadata; no versioned company/listing relation", "not company market capitalization or market WACC weights"}}, nil
}
