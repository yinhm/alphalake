package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"github.com/yinhm/alphalake/internal/source/safe"
)

func PublishHKDCNY(ctx context.Context, db *sql.DB, runID, artifactID int64, parserHash string, s safe.Snapshot) (int64, bool, error) {
	if err := safe.Validate(s); err != nil {
		return 0, false, err
	}
	p, err := beginReferencePublication(ctx, db, runID, artifactID, referenceInput{Source: safe.Source, Dataset: safe.Dataset, URL: safe.URL, Date: s.ObservationDate, SHA: s.SHA256, ParserVersion: s.ParserVersion, Runtime: s.Runtime, Normalization: "100-hkd-to-cny-v1", ParserHash: parserHash})
	if err != nil {
		return 0, false, err
	}
	defer p.tx.Rollback()
	if p.existing {
		var n int
		if err := p.tx.QueryRowContext(ctx, `SELECT count(*) FROM market.fx_rate WHERE release_id=?`, p.id).Scan(&n); err != nil {
			return 0, false, err
		}
		if n != len(s.Observations) {
			return 0, false, errors.New("published FX count changed")
		}
	}
	for _, o := range s.Observations {
		// Midnight Shanghai is only a carrier for a date-precision central parity.
		args := []any{p.id, artifactID, o.SourceLocator, o.RawValue, o.Date + "T00:00:00+08:00", o.Value}
		if p.existing {
			var n int
			err = p.tx.QueryRowContext(ctx, `SELECT count(*) FROM market.fx_rate WHERE release_id=? AND artifact_id=? AND source_locator=? AND raw_value=? AND observed_at=CAST(? AS TIMESTAMPTZ) AND value=CAST(? AS DECIMAL(38,12)) AND raw_unit='CNY_per_100_HKD' AND base_currency='HKD' AND quote_currency='CNY' AND time_precision='date' AND source_timezone='Asia/Shanghai' AND fixing_code='cfets_central_parity_safe' AND rate_type='midpoint'`, args...).Scan(&n)
			if err == nil && n != 1 {
				err = errors.New("published FX interpretation changed")
			}
		} else {
			_, err = p.tx.ExecContext(ctx, `INSERT INTO market.fx_rate(release_id,artifact_id,source_locator,raw_value,observed_at,value,raw_unit,base_currency,quote_currency,time_precision,source_timezone,fixing_code,rate_type) VALUES (?,?,?,?,CAST(? AS TIMESTAMPTZ),CAST(? AS DECIMAL(38,12)),'CNY_per_100_HKD','HKD','CNY','date','Asia/Shanghai','cfets_central_parity_safe','midpoint')`, args...)
		}
		if err != nil {
			return 0, false, err
		}
	}
	return p.finish(ctx)
}
