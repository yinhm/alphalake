package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"github.com/yinhm/alphalake/internal/source/damodaran"
)

func PublishCreditSpreads(ctx context.Context, db *sql.DB, runID, artifactID int64, parserHash string, s damodaran.CreditSnapshot) (int64, bool, error) {
	if err := damodaran.ValidateCredit(s); err != nil {
		return 0, false, err
	}
	p, err := beginReferencePublication(ctx, db, runID, artifactID, referenceInput{Source: damodaran.Source, Dataset: damodaran.CreditDataset, URL: damodaran.CreditURL, Date: s.ObservationDate, SHA: s.SHA256, ParserVersion: s.ParserVersion, Runtime: s.Runtime, Normalization: "credit-source-open-closed-v1", ParserHash: parserHash})
	if err != nil {
		return 0, false, err
	}
	defer p.tx.Rollback()
	if p.existing {
		var n int
		if err := p.tx.QueryRowContext(ctx, `SELECT count(*) FROM reference.credit_spread_band WHERE release_id=?`, p.id).Scan(&n); err != nil {
			return 0, false, err
		}
		if n != 15 {
			return 0, false, errors.New("published credit count changed")
		}
	}
	for _, o := range s.Observations {
		args := []any{p.id, artifactID, o.SourceLocator, o.RawValue, s.ObservationDate, o.Lower, o.Upper, o.Lower, o.Upper, o.Rating, o.Value}
		if p.existing {
			var n int
			err = p.tx.QueryRowContext(ctx, `SELECT count(*) FROM reference.credit_spread_band WHERE release_id=? AND artifact_id=? AND source_locator=? AND raw_value=? AND observation_date=? AND coverage_lower=CAST(? AS DECIMAL(38,12)) AND coverage_upper=CAST(? AS DECIMAL(38,12)) AND raw_lower=? AND raw_upper=? AND rating=? AND value=CAST(? AS DECIMAL(38,12)) AND raw_unit='percent' AND observation_precision='month' AND firm_type='large_nonfinancial' AND method_code='us_synthetic_rating_source_open_closed'`, args...).Scan(&n)
			if err != nil {
				return 0, false, err
			}
			if n != 1 {
				return 0, false, errors.New("published credit interpretation changed")
			}
		} else {
			_, err = p.tx.ExecContext(ctx, `INSERT INTO reference.credit_spread_band(release_id,artifact_id,source_locator,raw_value,observation_date,coverage_lower,coverage_upper,raw_lower,raw_upper,rating,value,raw_unit,observation_precision,firm_type,method_code) VALUES (?,?,?,?,?,CAST(? AS DECIMAL(38,12)),CAST(? AS DECIMAL(38,12)),?,?,?,CAST(? AS DECIMAL(38,12)),'percent','month','large_nonfinancial','us_synthetic_rating_source_open_closed')`, args...)
			if err != nil {
				return 0, false, err
			}
		}
	}
	return p.finish(ctx)
}
