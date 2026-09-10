package duckdb

import (
	"context"
	"database/sql"
	"errors"

	"github.com/yinhm/alphalake/internal/source/chinabond"
	"github.com/yinhm/alphalake/internal/source/damodaran"
)

func PublishIndustryBeta(ctx context.Context, db *sql.DB, runID, artifactID int64, parserHash string, s damodaran.BetaSnapshot) (int64, bool, error) {
	if err := damodaran.ValidateBeta(s); err != nil {
		return 0, false, err
	}
	return publishIndustryStats(ctx, db, runID, artifactID, referenceInput{Source: damodaran.Source, Dataset: damodaran.BetaDataset, URL: damodaran.BetaURL, Date: s.ObservationDate, SHA: s.SHA256, ParserVersion: s.ParserVersion, Runtime: s.Runtime, Normalization: "global-beta-decimal12-v1", ParserHash: parserHash}, s.Observations, func(o damodaran.IndustryObservation) string { return damodaran.BetaMethod(s, o) })
}

func PublishIndustryCapital(ctx context.Context, db *sql.DB, runID, artifactID int64, parserHash string, s damodaran.CapitalSnapshot) (int64, bool, error) {
	if err := damodaran.ValidateCapital(s); err != nil {
		return 0, false, err
	}
	return publishIndustryStats(ctx, db, runID, artifactID, referenceInput{Source: damodaran.Source, Dataset: damodaran.CapitalDataset, URL: damodaran.CapitalURL, Date: s.ObservationDate, SHA: s.SHA256, ParserVersion: s.ParserVersion, Runtime: s.Runtime, Normalization: "global-capital-decimal12-v1", ParserHash: parserHash}, s.Observations, func(damodaran.IndustryObservation) string { return damodaran.CapitalMethod })
}

// 两个已审核全球行业源复用相同的分类节点、不可变统计和原子完成键。
func publishIndustryStats(ctx context.Context, db *sql.DB, runID, artifactID int64, in referenceInput, observations []damodaran.IndustryObservation, method func(damodaran.IndustryObservation) string) (int64, bool, error) {
	p, err := beginReferencePublication(ctx, db, runID, artifactID, in)
	if err != nil {
		return 0, false, err
	}
	defer p.tx.Rollback()
	var taxonomy int64
	if !p.existing {
		_, err = p.tx.ExecContext(ctx, `INSERT INTO classification.taxonomy(source,taxonomy_code,name,taxonomy_type) VALUES (?,?,?,'industry') ON CONFLICT(source,taxonomy_code) DO NOTHING`, damodaran.Source, damodaran.BetaTaxonomy, "Damodaran industries 2026")
		if err != nil {
			return 0, false, err
		}
	}
	err = p.tx.QueryRowContext(ctx, `SELECT taxonomy_id FROM classification.taxonomy WHERE source=? AND taxonomy_code=? AND name=? AND taxonomy_type='industry'`, damodaran.Source, damodaran.BetaTaxonomy, "Damodaran industries 2026").Scan(&taxonomy)
	if err != nil {
		return 0, false, err
	}
	nodes := map[string]int64{}
	for _, o := range observations {
		if _, ok := nodes[o.Industry]; ok {
			continue
		}
		if !p.existing {
			_, err = p.tx.ExecContext(ctx, `INSERT INTO classification.node(taxonomy_id,source_node_code,name,level) VALUES (?,?,?,1) ON CONFLICT(taxonomy_id,source_node_code) DO NOTHING`, taxonomy, o.Industry, o.Industry)
			if err != nil {
				return 0, false, err
			}
		}
		var id int64
		err = p.tx.QueryRowContext(ctx, `SELECT node_id FROM classification.node WHERE taxonomy_id=? AND source_node_code=? AND name=? AND level=1 AND parent_node_id IS NULL`, taxonomy, o.Industry, o.Industry).Scan(&id)
		if err != nil {
			return 0, false, err
		}
		nodes[o.Industry] = id
	}
	if p.existing {
		var n int
		if err := p.tx.QueryRowContext(ctx, `SELECT count(*) FROM reference.industry_stat WHERE release_id=?`, p.id).Scan(&n); err != nil {
			return 0, false, err
		}
		if n != len(observations) {
			return 0, false, errors.New("published industry statistic count changed")
		}
	}
	for _, o := range observations {
		unit := "fraction"
		if o.MetricCode == "beta_unlevered" || o.MetricCode == "beta_unlevered_cash_adjusted" || o.MetricCode == damodaran.CapitalMetric {
			unit = "dimensionless"
		}
		args := []any{p.id, artifactID, o.SourceLocator, o.RawValue, unit, nodes[o.Industry], in.Date, o.MetricCode, method(o), o.SampleCount, o.Value}
		if p.existing {
			var n int
			err = p.tx.QueryRowContext(ctx, `SELECT count(*) FROM reference.industry_stat WHERE release_id=? AND artifact_id=? AND source_locator=? AND raw_value=? AND raw_unit=? AND industry_node_id=? AND observation_date=? AND metric_code=? AND method_code=? AND sample_count=? AND value=CAST(? AS DECIMAL(38,12)) AND sample_region='global' AND statistic_code='provider_estimate' AND value_status='reported'`, args...).Scan(&n)
			if err != nil {
				return 0, false, err
			}
			if n != 1 {
				return 0, false, errors.New("published industry statistic interpretation changed")
			}
		} else {
			_, err = p.tx.ExecContext(ctx, `INSERT INTO reference.industry_stat(release_id,artifact_id,source_locator,raw_value,raw_unit,industry_node_id,observation_date,metric_code,method_code,sample_count,value,sample_region,statistic_code,value_status) VALUES (?,?,?,?,?,?,?,?,?,?,CAST(? AS DECIMAL(38,12)),'global','provider_estimate','reported')`, args...)
			if err != nil {
				return 0, false, err
			}
		}
	}
	return p.finish(ctx)
}

func PublishCNYGovernmentYield(ctx context.Context, db *sql.DB, runID, artifactID int64, parserHash string, s chinabond.Snapshot) (int64, bool, error) {
	if err := chinabond.Validate(s); err != nil {
		return 0, false, err
	}
	p, err := beginReferencePublication(ctx, db, runID, artifactID, referenceInput{Source: chinabond.Source, Dataset: chinabond.Dataset, URL: chinabond.URL, Date: s.ObservationDate, SHA: s.SHA256, ParserVersion: s.ParserVersion, Runtime: s.Runtime, Normalization: "government-percent-to-fraction-v1", ParserHash: parserHash})
	if err != nil {
		return 0, false, err
	}
	defer p.tx.Rollback()
	if p.existing {
		var n int
		if err := p.tx.QueryRowContext(ctx, `SELECT count(*) FROM market.yield_curve_point WHERE release_id=?`, p.id).Scan(&n); err != nil {
			return 0, false, err
		}
		if n != 8 {
			return 0, false, errors.New("published yield count changed")
		}
	}
	for _, o := range s.Observations {
		args := []any{p.id, artifactID, o.SourceLocator, o.RawValue, chinabond.CurveCode, s.ObservationDate, o.TenorMonths, o.Value}
		if p.existing {
			var n int
			err = p.tx.QueryRowContext(ctx, `SELECT count(*) FROM market.yield_curve_point WHERE release_id=? AND artifact_id=? AND source_locator=? AND raw_value=? AND curve_code=? AND observation_date=? AND tenor_months=? AND value=CAST(? AS DECIMAL(38,12)) AND raw_unit='percent' AND currency='CNY' AND rate_type='yield_to_maturity' AND compounding='unknown' AND day_count='unknown'`, args...).Scan(&n)
			if err != nil {
				return 0, false, err
			}
			if n != 1 {
				return 0, false, errors.New("published yield interpretation changed")
			}
		} else {
			_, err = p.tx.ExecContext(ctx, `INSERT INTO market.yield_curve_point(release_id,artifact_id,source_locator,raw_value,curve_code,observation_date,tenor_months,value,raw_unit,currency,rate_type,compounding,day_count) VALUES (?,?,?,?,?,?,?,CAST(? AS DECIMAL(38,12)),'percent','CNY','yield_to_maturity','unknown','unknown')`, args...)
			if err != nil {
				return 0, false, err
			}
		}
	}
	return p.finish(ctx)
}
