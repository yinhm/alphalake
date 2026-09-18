package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"github.com/yinhm/alphalake/internal/source/hkex"
	"time"
)

func PublishHKEXQuote(ctx context.Context, db *sql.DB, run, artifact int64, hash string, s hkex.Snapshot) (int64, bool, error) {
	if e := hkex.Validate(s); e != nil {
		return 0, false, e
	}
	p, e := beginReferencePublication(ctx, db, run, artifact, referenceInput{Source: hkex.Source, Dataset: hkex.Dataset, URL: hkex.URL(s.ObservationDate), Date: s.ObservationDate, SHA: s.SHA256, ParserVersion: s.ParserVersion, Runtime: s.Runtime, Normalization: "unadjusted-closing-column-v1", ParserHash: hash})
	if e != nil {
		return 0, false, e
	}
	defer p.tx.Rollback()
	var fetched time.Time
	if e = p.tx.QueryRowContext(ctx, `SELECT fetched_at FROM meta.artifact WHERE artifact_id=?`, artifact).Scan(&fetched); e != nil {
		return 0, false, e
	}
	day, _ := time.Parse("2006-01-02", s.ObservationDate)
	if fetched.Before(day.Add(16 * time.Hour)) {
		return 0, false, errors.New("HK close must be acquired after local date end")
	}

	var count int
	var listing int64
	e = p.tx.QueryRowContext(ctx, `SELECT count(*),coalesce(min(l.listing_id),0) FROM core.listing l JOIN core.listing_identifier x ON x.listing_id=l.listing_id WHERE x.provider='hkex' AND x.identifier_type='symbol' AND x.identifier_value=? AND x.market_namespace='XHKG' AND l.exchange_mic='XHKG' AND l.trading_currency='HKD' AND x.valid_from<=? AND (x.valid_to IS NULL OR x.valid_to>?) AND l.valid_from<=? AND (l.valid_to IS NULL OR l.valid_to>?)`, s.Symbol, s.ObservationDate, s.ObservationDate, s.ObservationDate, s.ObservationDate).Scan(&count, &listing)
	if e != nil {
		return 0, false, e
	}
	if count != 1 {
		return 0, false, errors.New("review H-share listing before quote publication")
	}
	args := []any{p.id, artifact, listing, s.ObservationDate, s.Value, s.RawValue, s.SourceLocator}
	if p.existing {
		if e = p.tx.QueryRowContext(ctx, `SELECT count(*) FROM market.listing_close_observation WHERE release_id=?`, p.id).Scan(&count); e != nil {
			return 0, false, e
		}
		if count != 1 {
			return 0, false, errors.New("HK release scope changed")
		}
		e = p.tx.QueryRowContext(ctx, `SELECT count(*) FROM market.listing_close_observation WHERE release_id=? AND artifact_id=? AND listing_id=? AND trade_date=? AND close=CAST(? AS DECIMAL(20,6)) AND raw_value=? AND source_locator=? AND adjustment='unadjusted'`, args...).Scan(&count)
		if e == nil && count != 1 {
			e = errors.New("published HK quote changed")
		}
	} else {
		_, e = p.tx.ExecContext(ctx, `INSERT INTO market.listing_close_observation(release_id,artifact_id,listing_id,trade_date,close,raw_value,source_locator,adjustment) VALUES (?,?,?,?,CAST(? AS DECIMAL(20,6)),?,?,'unadjusted')`, args...)
	}
	if e != nil {
		return 0, false, e
	}
	return p.finish(ctx)
}
