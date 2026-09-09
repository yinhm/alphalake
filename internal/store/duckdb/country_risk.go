package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"github.com/yinhm/alphalake/internal/source/damodaran"
)

// PublishCountryRisk publishes only the reviewed complete selected-country scope.
func PublishCountryRisk(ctx context.Context, db *sql.DB, runID, artifactID int64, parserHash string, s damodaran.Snapshot) (int64, bool, error) {
	if err := damodaran.Validate(s); err != nil {
		return 0, false, err
	}
	p, err := beginReferencePublication(ctx, db, runID, artifactID, referenceInput{Source: damodaran.Source, Dataset: damodaran.Dataset, URL: damodaran.URL, Date: s.ObservationDate, SHA: s.WorkbookSHA256, ParserVersion: s.ParserVersion, Runtime: s.Runtime, Normalization: "country-risk-decimal12-v1", ParserHash: parserHash})
	if err != nil {
		return 0, false, err
	}
	defer p.tx.Rollback()
	if p.existing {
		// Replaying an interpretation must agree with all previously published rows.
		var count int
		if err = p.tx.QueryRowContext(ctx, `SELECT count(*) FROM reference.country_risk WHERE release_id=?`, p.id).Scan(&count); err != nil {
			return 0, false, err
		}
		if count != len(s.Observations) {
			return 0, false, errors.New("published country-risk row count changed")
		}
		for _, o := range s.Observations {
			var n int
			err = p.tx.QueryRowContext(ctx, `SELECT count(*) FROM reference.country_risk WHERE release_id=? AND artifact_id=? AND subject_kind=? AND subject_code=? AND metric_code=? AND observation_date=? AND method_code=? AND value_status='reported' AND raw_unit='fraction' AND source_locator=? AND raw_value=? AND value=CAST(? AS DECIMAL(38,12))`, p.id, artifactID, o.SubjectKind, o.SubjectCode, o.MetricCode, s.ObservationDate, damodaran.Method(o), o.SourceLocator, o.RawValue, o.Value).Scan(&n)
			if err != nil {
				return 0, false, err
			}
			if n != 1 {
				return 0, false, errors.New("published country-risk interpretation changed")
			}
		}
		return p.finish(ctx)
	}
	for _, o := range s.Observations {
		_, err = p.tx.ExecContext(ctx, `INSERT INTO reference.country_risk(release_id,artifact_id,source_locator,raw_value,raw_unit,subject_kind,subject_code,observation_date,metric_code,method_code,value,value_status) VALUES (?,?,?,?,'fraction',?,?,?,?,?,CAST(? AS DECIMAL(38,12)),'reported')`, p.id, artifactID, o.SourceLocator, o.RawValue, o.SubjectKind, o.SubjectCode, s.ObservationDate, o.MetricCode, damodaran.Method(o), o.Value)
		if err != nil {
			return 0, false, err
		}
	}
	return p.finish(ctx)
}
