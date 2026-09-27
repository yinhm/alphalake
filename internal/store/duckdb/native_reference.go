package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"github.com/yinhm/alphalake/internal/source/damodaran"
)

func PublishNativeReference(ctx context.Context, db *sql.DB, runID, artifactID int64, parserHash string, s damodaran.NativeReferenceSnapshot) (int64, bool, error) {
	if err := damodaran.ValidateNativeReference(s); err != nil {
		return 0, false, err
	}
	p, err := beginReferencePublication(ctx, db, runID, artifactID, referenceInput{Source: damodaran.Source, Dataset: damodaran.NativeReferenceDataset(s.FileStem), URL: damodaran.NativeReferenceURL(s.FileStem), Date: s.ObservationDate, SHA: s.SHA256, ParserVersion: s.ParserVersion, Runtime: s.Runtime, Normalization: "native-reference-decimal12-v1", ParserHash: parserHash})
	if err != nil {
		return 0, false, err
	}
	defer p.tx.Rollback()
	tax := s.FileStem == "countrytaxrates"
	table := "reference.industry_stat"
	if tax {
		table = "reference.country_tax"
	}
	var nodes map[string]int64
	if !tax {
		names := make([]string, 0, len(s.Observations))
		for _, o := range s.Observations {
			names = append(names, o.Subject)
		}
		nodes, err = damodaranIndustryNodes(ctx, p.tx, p.existing, names)
		if err != nil {
			return 0, false, err
		}
	}
	if p.existing {
		var n int
		if err = p.tx.QueryRowContext(ctx, `SELECT count(*) FROM `+table+` WHERE release_id=?`, p.id).Scan(&n); err != nil {
			return 0, false, err
		}
		if n != len(s.Observations) {
			return 0, false, errors.New("published native reference count changed")
		}
	}
	for _, o := range s.Observations {
		method := "provider_reported"
		if o.MetricCode == "beta_unlevered" || o.MetricCode == "beta_unlevered_cash_adjusted" {
			method = "provider_marginal_tax_" + s.UnleveringTaxRate
		}
		cols := "release_id,artifact_id,source_locator,raw_value,raw_unit,observation_date,metric_code,method_code,value,value_status"
		args := []any{p.id, artifactID, o.SourceLocator, o.RawValue, o.RawUnit, s.ObservationDate, o.MetricCode, method, o.Value, o.ValueStatus}
		placeholders := "?,?,?,?,?,?,?,?,CAST(? AS DECIMAL(38,12)),?"
		predicate := `release_id=? AND artifact_id=? AND source_locator=? AND raw_value=? AND raw_unit=? AND observation_date=? AND metric_code=? AND method_code=? AND value IS NOT DISTINCT FROM CAST(? AS DECIMAL(38,12)) AND value_status=?`
		if tax {
			cols += ",subject_code"
			placeholders += ",?"
			predicate += " AND subject_code=?"
			args = append(args, o.Subject)
		} else {
			cols += ",industry_node_id,sample_region,statistic_code,sample_count"
			placeholders += ",?,?,?,?"
			predicate += " AND industry_node_id=? AND sample_region=? AND statistic_code=? AND sample_count IS NOT DISTINCT FROM ?"
			args = append(args, nodes[o.Subject], s.SampleRegion, "provider_estimate", o.SampleCount)
		}
		if p.existing {
			var n int
			err = p.tx.QueryRowContext(ctx, "SELECT count(*) FROM "+table+" WHERE "+predicate, args...).Scan(&n)
			if err == nil && n != 1 {
				err = errors.New("published native reference interpretation changed")
			}
		} else {
			_, err = p.tx.ExecContext(ctx, "INSERT INTO "+table+"("+cols+") VALUES ("+placeholders+")", args...)
		}
		if err != nil {
			return 0, false, err
		}
	}
	return p.finish(ctx)
}
