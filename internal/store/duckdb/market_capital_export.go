package duckdb

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/yinhm/alphalake/internal/source/capital"
	"github.com/yinhm/alphalake/internal/source/hkex"
	"github.com/yinhm/alphalake/internal/source/safe"
	"time"
)

// ExportMarketCapital fixes issuer/class, quote and FX evidence in one read transaction.
// Prices are exact-date. Share carry-forward is not hidden: the consumer must set an age limit.
func ExportMarketCapital(ctx context.Context, db *sql.DB, code string, day, asof time.Time, shares, hk, fx int64) (map[string]any, error) {
	if capital.URL(code) == "" || day.IsZero() || asof.IsZero() || shares <= 0 {
		return nil, errors.New("reviewed code, market date, cutoff and share release required")
	}
	if (code == "300866" && (hk <= 0 || fx <= 0)) || (code == "600519" && (hk != 0 || fx != 0)) {
		return nil, errors.New("exact issuer-class release scope required")
	}
	tx, e := db.BeginTx(ctx, nil)
	if e != nil {
		return nil, e
	}
	defer tx.Rollback()
	read := func(query string, args ...any) ([]json.RawMessage, error) {
		var raw sql.NullString
		err := tx.QueryRowContext(ctx, `SELECT CAST(to_json(list(q)) AS VARCHAR) FROM (`+query+`) q`, args...).Scan(&raw)
		if err != nil {
			return nil, err
		}
		if !raw.Valid {
			return nil, errors.New("missing market observations")
		}
		var rows []json.RawMessage
		err = json.Unmarshal([]byte(raw.String), &rows)
		return rows, err
	}
	out := map[string]any{"contract_version": "alphalake-market-capital-v1", "code": code, "market_date": day.Format("2006-01-02"), "information_as_of": asof.UTC().Format(time.RFC3339Nano), "currency": "CNY"}
	var releases []json.RawMessage
	for _, pin := range []struct {
		id              int64
		source, dataset string
	}{{shares, capital.Source, capital.Dataset + "/" + code}, {hk, hkex.Source, hkex.Dataset}, {fx, safe.Source, safe.Dataset}} {
		if pin.id == 0 {
			continue
		}
		rows, err := read(`SELECT r.release_id,r.source,r.dataset,r.content_key,r.parser_version,r.normalization_version,CAST(r.available_at AS VARCHAR) AS available_at,CAST(r.recorded_at AS VARCHAR) AS recorded_at,a.artifact_id,a.sha256 AS artifact_sha256,a.source_locator AS source_url
   FROM meta.dataset_release r JOIN meta.dataset_release_artifact l ON l.release_id=r.release_id AND l.role='data'
   JOIN meta.artifact a ON a.artifact_id=l.artifact_id AND a.source=r.source AND a.dataset=r.dataset
   JOIN meta.checkpoint c ON c.source=r.source AND c.dataset=r.dataset AND c.checkpoint_key=r.content_key AND c.checkpoint_value=CAST(r.release_id AS VARCHAR)
   JOIN meta.ingest_run ir ON ir.ingest_run_id=r.ingest_run_id AND ir.status='completed'
   WHERE r.release_id=? AND r.source=? AND r.dataset=? AND r.available_at<=? AND r.recorded_at<=? AND ir.finished_at<=?`, pin.id, pin.source, pin.dataset, asof, asof, asof)
		if err != nil {
			return nil, err
		}
		if len(rows) != 1 {
			return nil, fmt.Errorf("invalid market release %d", pin.id)
		}
		releases = append(releases, rows...)
	}
	out["releases"] = releases
	rows, e := read(`SELECT s.observation_id,s.release_id,s.artifact_id,s.instrument_id,i.company_id,s.share_basis,CAST(s.effective_date AS VARCHAR) AS effective_date,CAST(s.value AS VARCHAR) AS value,s.source_locator,l.listing_id,l.exchange_mic,l.trading_currency,x.provider,x.identifier_value AS symbol,
 CAST(l.valid_from AS VARCHAR) AS listing_valid_from,CAST(l.recorded_at AS VARCHAR) AS identity_recorded_at
 FROM market.share_count_observation s JOIN ref.instrument i ON i.instrument_id=s.instrument_id
 JOIN ref.company co ON co.company_id=i.company_id
 JOIN ref.listing l ON l.instrument_id=i.instrument_id AND l.artifact_id=s.artifact_id
 JOIN ref.listing_identifier x ON x.listing_id=l.listing_id AND x.artifact_id=s.artifact_id
 JOIN meta.dataset_release_artifact a ON a.release_id=s.release_id AND a.artifact_id=s.artifact_id AND a.role='data'
 WHERE s.release_id=? AND s.scope='share_class' AND s.effective_date<=? AND l.valid_from<=? AND (l.valid_to IS NULL OR l.valid_to>?) AND x.valid_from<=? AND (x.valid_to IS NULL OR x.valid_to>?) AND l.recorded_at<=? AND x.recorded_at<=?
 ORDER BY s.instrument_id,s.share_basis`, shares, day, day, day, day, day, asof, asof)
	if e != nil {
		return nil, e
	}
	n := 3
	if code == "300866" {
		n = 6
	}
	if len(rows) != n {
		return nil, errors.New("incomplete/ambiguous issuer classes")
	}
	out["share_counts"] = rows
	symbol := "sh600519"
	if code == "300866" {
		symbol = "sz300866"
	}
	quote, e := exportValuationQuoteOnTx(ctx, tx, symbol, day, asof)
	if e != nil {
		return nil, e
	}
	out["a_quote"] = quote
	if hk > 0 {
		rows, e = read(`SELECT o.observation_id,o.release_id,o.artifact_id,o.listing_id,l.instrument_id,CAST(o.trade_date AS VARCHAR) AS trade_date,CAST(o.close AS VARCHAR) AS close,o.raw_value,o.source_locator,o.adjustment,l.trading_currency AS currency
   FROM market.listing_close_observation o JOIN ref.listing l ON l.listing_id=o.listing_id JOIN meta.dataset_release_artifact a ON a.release_id=o.release_id AND a.artifact_id=o.artifact_id AND a.role='data'
   WHERE o.release_id=? AND o.trade_date=?`, hk, day)
		if e != nil {
			return nil, e
		}
		if len(rows) != 1 {
			return nil, errors.New("missing exact-date H close")
		}
		out["h_quote"] = rows[0]
		rows, e = read(`SELECT o.observation_id,o.release_id,o.artifact_id,o.base_currency,o.quote_currency,CAST(o.observed_at AS VARCHAR) AS observed_at,o.time_precision,o.source_timezone,o.fixing_code,o.rate_type,CAST(o.value AS VARCHAR) AS value,o.raw_value,o.raw_unit,o.source_locator
   FROM market.fx_rate o JOIN meta.dataset_release_artifact a ON a.release_id=o.release_id AND a.artifact_id=o.artifact_id AND a.role='data'
   WHERE o.release_id=? AND CAST(o.observed_at AT TIME ZONE 'Asia/Shanghai' AS DATE)=? AND o.base_currency='HKD' AND o.quote_currency='CNY' AND o.rate_type='midpoint' AND o.fixing_code='cfets_central_parity_safe'`, fx, day)
		if e != nil {
			return nil, e
		}
		if len(rows) != 1 {
			return nil, errors.New("missing exact-date reviewed FX")
		}
		out["fx"] = rows[0]
	}
	if e = tx.Commit(); e != nil {
		return nil, e
	}
	return out, nil
}
