package duckdb

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/yinhm/alphalake/internal/source/chinabond"
	"github.com/yinhm/alphalake/internal/source/damodaran"
)

// ExportWACCReferences selects explicit complete releases; it does not infer
// 'latest' from arrival order. Optional recordedCutoff reproduces system knowledge.
func ExportWACCReferences(ctx context.Context, db *sql.DB, asof time.Time, recordedCutoff *time.Time, country, beta, yield int64) (map[string]any, error) {
	if db == nil || asof.IsZero() || country <= 0 || beta <= 0 || yield <= 0 || country == beta || country == yield || beta == yield {
		return nil, errors.New("ASOF and three distinct positive release IDs required")
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	for _, selection := range []struct {
		id              int64
		source, dataset string
	}{{country, damodaran.Source, damodaran.Dataset}, {beta, damodaran.Source, damodaran.BetaDataset}, {yield, chinabond.Source, chinabond.Dataset}} {
		var n int
		err = tx.QueryRowContext(ctx, `SELECT count(*) FROM meta.dataset_release r
    JOIN meta.dataset_release_artifact l ON l.release_id=r.release_id AND l.role='data'
    JOIN meta.artifact a ON a.artifact_id=l.artifact_id AND a.source=r.source AND a.dataset=r.dataset
    JOIN meta.checkpoint c ON c.source=r.source AND c.dataset=r.dataset AND c.checkpoint_key=r.content_key AND c.checkpoint_value=CAST(r.release_id AS VARCHAR)
    WHERE r.release_id=? AND r.source=? AND r.dataset=? AND r.available_at<=?
    AND (? IS NULL OR r.recorded_at<=CAST(? AS TIMESTAMPTZ))`, selection.id, selection.source, selection.dataset, asof, recordedCutoff, recordedCutoff).Scan(&n)
		if err != nil {
			return nil, err
		}
		if n != 1 {
			return nil, fmt.Errorf("release %d missing, ambiguous, unavailable at cutoff, or has broken lineage", selection.id)
		}
	}
	output := map[string]any{"contract_version": "alphalake-wacc-references-v1", "information_as_of": asof.UTC().Format(time.RFC3339Nano), "recorded_cutoff": recordedCutoff}
	for _, q := range []struct {
		name, query string
		args        []any
		count       int
	}{
		{"releases", `SELECT r.release_id,r.source,r.dataset,r.source_version,r.content_key,r.parser_version,r.normalization_version,
    CAST(r.available_at AS VARCHAR) AS available_at,CAST(r.recorded_at AS VARCHAR) AS recorded_at,r.availability_basis,
    a.artifact_id,a.sha256 AS artifact_sha256,a.source_locator AS source_url
    FROM meta.dataset_release r JOIN meta.dataset_release_artifact l ON l.release_id=r.release_id AND l.role='data'
    JOIN meta.artifact a ON a.artifact_id=l.artifact_id WHERE r.release_id IN (?,?,?) ORDER BY r.release_id`, []any{country, beta, yield}, 3},
		{"country_risk", `SELECT o.observation_id,o.release_id,o.artifact_id,o.subject_kind,o.subject_code,o.metric_code,o.method_code,
    CAST(o.observation_date AS VARCHAR) AS observation_date,CAST(o.value AS VARCHAR) AS value,o.value_status,o.raw_value,o.raw_unit,o.source_locator
    FROM reference.country_risk o JOIN meta.dataset_release_artifact l ON l.release_id=o.release_id AND l.artifact_id=o.artifact_id AND l.role='data'
    WHERE o.release_id=? AND o.observation_date<=CAST(? AS DATE) ORDER BY o.subject_code,o.metric_code`, []any{country, asof}, 10},
		{"industry_stats", `SELECT o.observation_id,o.release_id,o.artifact_id,n.source_node_code AS industry,n.name AS industry_name,t.taxonomy_code,
    o.sample_region,o.metric_code,o.method_code,o.statistic_code,o.sample_count,CAST(o.observation_date AS VARCHAR) AS observation_date,
    CAST(o.value AS VARCHAR) AS value,o.value_status,o.raw_value,o.raw_unit,o.source_locator
    FROM reference.industry_stat o JOIN classification.node n ON n.node_id=o.industry_node_id
    JOIN classification.taxonomy t ON t.taxonomy_id=n.taxonomy_id
    JOIN meta.dataset_release_artifact l ON l.release_id=o.release_id AND l.artifact_id=o.artifact_id AND l.role='data'
    WHERE o.release_id=? AND o.observation_date<=CAST(? AS DATE) AND t.source='damodaran' AND t.taxonomy_code='damodaran_industry_2026'
    ORDER BY n.source_node_code,o.metric_code`, []any{beta, asof}, 376},
		{"yield_curve", `SELECT o.observation_id,o.release_id,o.artifact_id,o.curve_code,o.currency,o.tenor_months,o.rate_type,o.compounding,o.day_count,
    CAST(o.observation_date AS VARCHAR) AS observation_date,CAST(o.value AS VARCHAR) AS value,o.raw_value,o.raw_unit,o.source_locator
    FROM market.yield_curve_point o JOIN meta.dataset_release_artifact l ON l.release_id=o.release_id AND l.artifact_id=o.artifact_id AND l.role='data'
    WHERE o.release_id=? AND o.observation_date<=CAST(? AS DATE) ORDER BY o.tenor_months`, []any{yield, asof}, 8},
	} {
		var raw sql.NullString
		if err := tx.QueryRowContext(ctx, `SELECT CAST(to_json(list(x)) AS VARCHAR) FROM (`+q.query+`) x`, q.args...).Scan(&raw); err != nil {
			return nil, err
		}
		var rows []json.RawMessage
		if !raw.Valid {
			return nil, fmt.Errorf("missing %s", q.name)
		}
		if err := json.Unmarshal([]byte(raw.String), &rows); err != nil {
			return nil, err
		}
		if len(rows) != q.count {
			return nil, fmt.Errorf("incomplete %s: %d/%d", q.name, len(rows), q.count)
		}
		output[q.name] = rows
	}
	if err := tx.Commit(); err != nil {
		return nil, err
	}
	return output, nil
}
