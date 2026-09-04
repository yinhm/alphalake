package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
)

const (
	fundamentalMaterializerV1 = "pit-fundamental-v1"
	fundamentalNormalizationV1 = "tdx-float32-decimal-v1"
	fundamentalFactStage       = "_alphalake_fundamental_fact_stage"
	fundamentalRejectStage     = "_alphalake_fundamental_reject_stage"
)

type CanonicalFundamentalResult struct {
	Candidates   int
	Materialized int
	Inserted     int
	Updated      int
	Removed      int
	Rejected     int
}

// MaterializeCanonicalFundamentals reconciles canonical PIT facts from linked
// provider facts plus authoritative filing evidence. Only explicitly reviewed
// field mappings with known units are eligible. The operation is deterministic
// and set-based: canonical rows are inserted, corrected in place by immutable raw
// identity, or removed if their source record is no longer safely materializable.
func MaterializeCanonicalFundamentals(ctx context.Context, db *sql.DB, ingestRunID int64, providerSource string) (CanonicalFundamentalResult, error) {
	var result CanonicalFundamentalResult
	if db == nil {
		return result, errors.New("duckdb is nil")
	}
	if ingestRunID <= 0 {
		return result, errors.New("ingest run ID must be positive")
	}
	providerSource = strings.TrimSpace(providerSource)
	if providerSource == "" {
		return result, errors.New("provider source is required")
	}

	conn, err := db.Conn(ctx)
	if err != nil {
		return result, fmt.Errorf("acquire fundamental materialization connection: %w", err)
	}
	defer conn.Close()
	for _, table := range []string{fundamentalFactStage, fundamentalRejectStage} {
		if _, err := conn.ExecContext(ctx, `DROP TABLE IF EXISTS temp.main.`+table); err != nil {
			return result, fmt.Errorf("cleanup fundamental stage %s: %w", table, err)
		}
	}
	if _, err := conn.ExecContext(ctx, `BEGIN TRANSACTION`); err != nil {
		return result, fmt.Errorf("begin fundamental materialization: %w", err)
	}
	committed := false
	defer func() {
		if !committed {
			_, _ = conn.ExecContext(context.Background(), `ROLLBACK`)
		}
		for _, table := range []string{fundamentalFactStage, fundamentalRejectStage} {
			_, _ = conn.ExecContext(context.Background(), `DROP TABLE IF EXISTS temp.main.`+table)
		}
	}()

	// Multiple simultaneously-active mappings for the same provider field would
	// make canonical semantics depend on arbitrary join order. Reject that as
	// catalogue corruption before changing any canonical row.
	var ambiguousMappings int
	if err := conn.QueryRowContext(ctx, `
		SELECT count(*)
		FROM (
			SELECT pf.provider_fact_id
			FROM fundamental.provider_fact pf
			JOIN fundamental.provider_field m
			  ON m.source=pf.source
			 AND m.provider_field=pf.provider_field
			 AND (m.valid_from IS NULL OR m.valid_from <= pf.report_period)
			 AND (m.valid_to IS NULL OR m.valid_to > pf.report_period)
			WHERE pf.source=?
			  AND m.canonical_field IS NOT NULL
			GROUP BY pf.provider_fact_id
			HAVING count(*) > 1
		)
	`, providerSource).Scan(&ambiguousMappings); err != nil {
		return result, fmt.Errorf("validate provider field mapping intervals: %w", err)
	}
	if ambiguousMappings != 0 {
		return result, fmt.Errorf("%d provider facts have overlapping canonical field mappings", ambiguousMappings)
	}

	if _, err := conn.ExecContext(ctx, `
		CREATE TEMP TABLE `+fundamentalRejectStage+` AS
		WITH candidates AS (
			SELECT
				pf.provider_fact_id,
				pf.instrument_id,
				pf.source AS primary_source,
				pf.revision_key,
				pf.provider_code,
				pf.provider_field,
				pf.report_period,
				pf.value,
				m.canonical_field,
				m.unit,
				m.value_kind,
				l.filing_id,
				f.instrument_id AS filing_instrument_id,
				f.report_period AS filing_report_period,
				f.announcement_time,
				f.filing_type
			FROM fundamental.provider_fact pf
			JOIN fundamental.provider_filing_link l
			  ON l.provider_source=pf.source
			 AND l.provider_revision_key=pf.revision_key
			 AND l.provider_code=pf.provider_code
			 AND l.status='linked'
			JOIN fundamental.filing f
			  ON f.filing_id=l.filing_id
			 AND f.resolution_status='resolved'
			JOIN fundamental.provider_field m
			  ON m.source=pf.source
			 AND m.provider_field=pf.provider_field
			 AND (m.valid_from IS NULL OR m.valid_from <= pf.report_period)
			 AND (m.valid_to IS NULL OR m.valid_to > pf.report_period)
			WHERE pf.source=?
			  AND m.canonical_field IS NOT NULL
		)
		SELECT
			*,
			CASE
				WHEN instrument_id <> filing_instrument_id THEN 'filing_instrument_mismatch'
				WHEN report_period <> filing_report_period THEN 'filing_report_period_mismatch'
				WHEN announcement_time < report_period THEN 'announcement_before_report_period'
				WHEN filing_type <> CASE
					WHEN month(report_period)=3 AND day(report_period)=31 THEN 'quarterly_q1'
					WHEN month(report_period)=6 AND day(report_period)=30 THEN 'semiannual'
					WHEN month(report_period)=9 AND day(report_period)=30 THEN 'quarterly_q3'
					WHEN month(report_period)=12 AND day(report_period)=31 THEN 'annual'
					ELSE 'unknown' END THEN 'filing_type_mismatch'
				WHEN value IS NULL OR NOT isfinite(value) THEN 'provider_value_not_finite'
				WHEN value_kind NOT IN ('monetary','shares') OR unit IS NULL OR trim(unit)='' THEN 'canonical_unit_unknown'
				WHEN try_cast(value AS DECIMAL(38,10)) IS NULL THEN 'canonical_decimal_overflow'
				ELSE NULL
			END AS rejection_rule
		FROM candidates
	`, providerSource); err != nil {
		return result, fmt.Errorf("build fundamental rejection stage: %w", err)
	}

	if err := conn.QueryRowContext(ctx, `SELECT count(*) FROM temp.main.`+fundamentalRejectStage).Scan(&result.Candidates); err != nil {
		return result, fmt.Errorf("count canonical fundamental candidates: %w", err)
	}
	if err := conn.QueryRowContext(ctx, `
		SELECT count(*) FROM temp.main.`+fundamentalRejectStage+` WHERE rejection_rule IS NOT NULL
	`).Scan(&result.Rejected); err != nil {
		return result, fmt.Errorf("count rejected canonical fundamentals: %w", err)
	}

	if _, err := conn.ExecContext(ctx, `
		CREATE TEMP TABLE `+fundamentalFactStage+` AS
		SELECT
			instrument_id,
			canonical_field,
			report_period,
			announcement_time,
			CASE
				WHEN month(report_period)=3 AND day(report_period)=31 THEN 'Q1'
				WHEN month(report_period)=6 AND day(report_period)=30 THEN 'H1'
				WHEN month(report_period)=9 AND day(report_period)=30 THEN 'Q3'
				WHEN month(report_period)=12 AND day(report_period)=31 THEN 'FY'
				ELSE 'unknown'
			END AS period_type,
			'provider_default' AS statement_scope,
			CASE WHEN value_kind='monetary' THEN 'CNY' ELSE NULL END AS currency,
			unit,
			cast(value AS DECIMAL(38,10)) AS value,
			primary_source,
			provider_field AS source_provider_field,
			provider_code,
			provider_fact_id,
			filing_id,
			revision_key,
			? AS normalization_rule,
			? AS materializer_version,
			?::BIGINT AS ingest_run_id
		FROM temp.main.`+fundamentalRejectStage+`
		WHERE rejection_rule IS NULL
	`, fundamentalNormalizationV1, fundamentalMaterializerV1, ingestRunID); err != nil {
		return result, fmt.Errorf("build canonical fundamental stage: %w", err)
	}
	if err := conn.QueryRowContext(ctx, `SELECT count(*) FROM temp.main.`+fundamentalFactStage).Scan(&result.Materialized); err != nil {
		return result, fmt.Errorf("count materializable canonical fundamentals: %w", err)
	}

	if err := conn.QueryRowContext(ctx, `
		SELECT count(*)
		FROM temp.main.`+fundamentalFactStage+` s
		WHERE NOT EXISTS (
			SELECT 1 FROM fundamental.fact f
			WHERE f.primary_source=s.primary_source
			  AND f.revision_key=s.revision_key
			  AND f.provider_code=s.provider_code
			  AND f.source_provider_field=s.source_provider_field
		)
	`).Scan(&result.Inserted); err != nil {
		return result, fmt.Errorf("count inserted canonical fundamentals: %w", err)
	}
	if err := conn.QueryRowContext(ctx, `
		SELECT count(*)
		FROM temp.main.`+fundamentalFactStage+` s
		JOIN fundamental.fact f
		  ON f.primary_source=s.primary_source
		 AND f.revision_key=s.revision_key
		 AND f.provider_code=s.provider_code
		 AND f.source_provider_field=s.source_provider_field
		WHERE f.instrument_id IS DISTINCT FROM s.instrument_id
		   OR f.canonical_field IS DISTINCT FROM s.canonical_field
		   OR f.report_period IS DISTINCT FROM s.report_period
		   OR f.announcement_time IS DISTINCT FROM s.announcement_time
		   OR f.period_type IS DISTINCT FROM s.period_type
		   OR f.statement_scope IS DISTINCT FROM s.statement_scope
		   OR f.currency IS DISTINCT FROM s.currency
		   OR f.unit IS DISTINCT FROM s.unit
		   OR f.value IS DISTINCT FROM s.value
		   OR f.provider_fact_id IS DISTINCT FROM s.provider_fact_id
		   OR f.source_filing_id IS DISTINCT FROM s.filing_id
		   OR f.normalization_rule IS DISTINCT FROM s.normalization_rule
		   OR f.materializer_version IS DISTINCT FROM s.materializer_version
	`).Scan(&result.Updated); err != nil {
		return result, fmt.Errorf("count updated canonical fundamentals: %w", err)
	}

	if _, err := conn.ExecContext(ctx, `
		INSERT INTO fundamental.fact (
			instrument_id, canonical_field, report_period, announcement_time,
			period_type, statement_scope, currency, unit, value,
			primary_source, source_provider_field, provider_code,
			provider_fact_id, source_filing_id, revision_key,
			normalization_rule, materializer_version, ingest_run_id
		)
		SELECT
			instrument_id, canonical_field, report_period, announcement_time,
			period_type, statement_scope, currency, unit, value,
			primary_source, source_provider_field, provider_code,
			provider_fact_id, filing_id, revision_key,
			normalization_rule, materializer_version, ingest_run_id
		FROM temp.main.`+fundamentalFactStage+`
		ON CONFLICT(primary_source, revision_key, provider_code, source_provider_field) DO UPDATE SET
			instrument_id=excluded.instrument_id,
			canonical_field=excluded.canonical_field,
			report_period=excluded.report_period,
			announcement_time=excluded.announcement_time,
			period_type=excluded.period_type,
			statement_scope=excluded.statement_scope,
			currency=excluded.currency,
			unit=excluded.unit,
			value=excluded.value,
			provider_fact_id=excluded.provider_fact_id,
			source_filing_id=excluded.source_filing_id,
			normalization_rule=excluded.normalization_rule,
			materializer_version=excluded.materializer_version,
			ingest_run_id=excluded.ingest_run_id,
			ingested_at=now()
	`); err != nil {
		return result, fmt.Errorf("merge canonical fundamental facts: %w", err)
	}

	if err := conn.QueryRowContext(ctx, `
		SELECT count(*)
		FROM fundamental.fact f
		WHERE f.primary_source=?
		  AND f.provider_code IS NOT NULL
		  AND f.materializer_version <> 'legacy'
		  AND NOT EXISTS (
			SELECT 1 FROM temp.main.`+fundamentalFactStage+` s
			WHERE s.primary_source=f.primary_source
			  AND s.revision_key=f.revision_key
			  AND s.provider_code=f.provider_code
			  AND s.source_provider_field=f.source_provider_field
		  )
	`, providerSource).Scan(&result.Removed); err != nil {
		return result, fmt.Errorf("count stale canonical fundamental facts: %w", err)
	}
	if _, err := conn.ExecContext(ctx, `
		DELETE FROM fundamental.fact f
		WHERE f.primary_source=?
		  AND f.provider_code IS NOT NULL
		  AND f.materializer_version <> 'legacy'
		  AND NOT EXISTS (
			SELECT 1 FROM temp.main.`+fundamentalFactStage+` s
			WHERE s.primary_source=f.primary_source
			  AND s.revision_key=f.revision_key
			  AND s.provider_code=f.provider_code
			  AND s.source_provider_field=f.source_provider_field
		  )
	`, providerSource); err != nil {
		return result, fmt.Errorf("remove stale canonical fundamental facts: %w", err)
	}

	if _, err := conn.ExecContext(ctx, `
		INSERT INTO meta.validation_result (
			ingest_run_id, source, dataset, rule_code, severity,
			subject_type, subject_key, observed_value, expected_value,
			passed, details
		)
		SELECT
			?, 'alphalake', 'fundamental_fact', rejection_rule, 'error',
			'provider_fact', cast(provider_fact_id AS VARCHAR),
			cast(value AS VARCHAR), canonical_field,
			false,
			concat('source=', primary_source,
			       ' revision=', revision_key,
			       ' code=', coalesce(provider_code,''),
			       ' field=', provider_field,
			       ' filing=', cast(filing_id AS VARCHAR))
		FROM temp.main.`+fundamentalRejectStage+`
		WHERE rejection_rule IS NOT NULL
	`, ingestRunID); err != nil {
		return result, fmt.Errorf("record canonical fundamental rejections: %w", err)
	}

	for _, table := range []string{fundamentalFactStage, fundamentalRejectStage} {
		if _, err := conn.ExecContext(ctx, `DROP TABLE temp.main.`+table); err != nil {
			return result, fmt.Errorf("drop fundamental stage %s: %w", table, err)
		}
	}
	if _, err := conn.ExecContext(ctx, `COMMIT`); err != nil {
		return result, fmt.Errorf("commit canonical fundamental materialization: %w", err)
	}
	committed = true
	return result, nil
}
