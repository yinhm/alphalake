package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
)

const (
	ProviderFilingLinked    = "linked"
	ProviderFilingPending   = "pending"
	ProviderFilingAmbiguous = "ambiguous"
	providerFilingLinkerV1  = "provider-filing-link-v1"
	providerFilingStage     = "_alphalake_provider_filing_link_stage"
)

type ProviderFilingLinkResult struct {
	Records   int
	Linked    int
	Pending   int
	Ambiguous int
	Removed   int
}

// RefreshProviderFilingLinks deterministically links each immutable provider
// financial record revision to authoritative filing metadata. A filing is
// eligible only when instrument, report period and periodic-report type match,
// and when its announcement was already public by the first observation of the
// provider artifact. This prevents later corrections from leaking backwards.
func RefreshProviderFilingLinks(ctx context.Context, db *sql.DB, ingestRunID int64, providerSource string) (ProviderFilingLinkResult, error) {
	var result ProviderFilingLinkResult
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
		return result, fmt.Errorf("acquire provider-filing link connection: %w", err)
	}
	defer conn.Close()
	if _, err := conn.ExecContext(ctx, `DROP TABLE IF EXISTS temp.main.`+providerFilingStage); err != nil {
		return result, fmt.Errorf("cleanup provider-filing stage: %w", err)
	}
	if _, err := conn.ExecContext(ctx, `BEGIN TRANSACTION`); err != nil {
		return result, fmt.Errorf("begin provider-filing link refresh: %w", err)
	}
	committed := false
	defer func() {
		if !committed {
			_, _ = conn.ExecContext(context.Background(), `ROLLBACK`)
		}
		_, _ = conn.ExecContext(context.Background(), `DROP TABLE IF EXISTS temp.main.`+providerFilingStage)
	}()

	if _, err := conn.ExecContext(ctx, `
		CREATE TEMP TABLE `+providerFilingStage+` AS
		WITH provider_records AS (
			SELECT
				pf.source AS provider_source,
				pf.revision_key AS provider_revision_key,
				pf.artifact_id AS provider_artifact_id,
				pf.provider_code,
				pf.report_period,
				pf.instrument_id,
				a.fetched_at AS provider_observed_at,
				CASE
					WHEN month(pf.report_period)=3 AND day(pf.report_period)=31 THEN 'quarterly_q1'
					WHEN month(pf.report_period)=6 AND day(pf.report_period)=30 THEN 'semiannual'
					WHEN month(pf.report_period)=9 AND day(pf.report_period)=30 THEN 'quarterly_q3'
					WHEN month(pf.report_period)=12 AND day(pf.report_period)=31 THEN 'annual'
					ELSE 'unknown'
				END AS required_filing_type
			FROM fundamental.provider_fact pf
			JOIN meta.artifact a ON a.artifact_id=pf.artifact_id
			WHERE pf.source=?
			  AND pf.provider_code IS NOT NULL
			  AND pf.artifact_id IS NOT NULL
			GROUP BY ALL
		),
		eligible_filings AS (
			SELECT
				f.*,
				CASE f.filing_variant
					WHEN 'corrected_report' THEN 4
					WHEN 'revision' THEN 3
					WHEN 'correction_notice' THEN 2
					WHEN 'full' THEN 1
					ELSE 0
				END AS variant_priority
			FROM fundamental.filing f
			WHERE f.source='cninfo'
			  AND f.resolution_status='resolved'
			  AND f.instrument_id IS NOT NULL
			  AND f.report_period IS NOT NULL
			  AND f.announcement_time IS NOT NULL
			  AND f.filing_variant IN ('full','corrected_report','revision','correction_notice')
		),
		candidate_rows AS (
			SELECT
				r.*,
				f.filing_id,
				f.announcement_time,
				f.variant_priority,
				count(f.filing_id) OVER (
					PARTITION BY r.provider_source, r.provider_revision_key, r.provider_code
				) AS candidate_count,
				count(f.filing_id) OVER (
					PARTITION BY r.provider_source, r.provider_revision_key, r.provider_code,
					             f.announcement_time, f.variant_priority
				) AS tie_count,
				row_number() OVER (
					PARTITION BY r.provider_source, r.provider_revision_key, r.provider_code
					ORDER BY f.announcement_time DESC NULLS LAST,
					         f.variant_priority DESC NULLS LAST,
					         f.filing_id DESC NULLS LAST
				) AS candidate_rank
			FROM provider_records r
			LEFT JOIN eligible_filings f
			  ON f.instrument_id=r.instrument_id
			 AND f.report_period=r.report_period
			 AND f.filing_type=r.required_filing_type
			 AND f.announcement_time <= r.provider_observed_at
		)
		SELECT
			provider_source,
			provider_revision_key,
			provider_artifact_id,
			provider_code,
			report_period,
			instrument_id,
			CASE WHEN candidate_count>0 AND tie_count=1 THEN filing_id ELSE NULL END AS filing_id,
			CASE
				WHEN candidate_count=0 THEN 'pending'
				WHEN tie_count>1 THEN 'ambiguous'
				ELSE 'linked'
			END AS status,
			candidate_count,
			CASE WHEN candidate_count>0 AND tie_count=1 THEN 'instrument_report_period_observed_at' ELSE NULL END AS link_method,
			CASE
				WHEN candidate_count=0 THEN 'no eligible filing announced by provider revision observation time'
				WHEN tie_count>1 THEN 'multiple equally-ranked authoritative filings'
				ELSE NULL
			END AS reason,
			? AS linker_version,
			CASE WHEN candidate_count>0 AND tie_count=1 THEN now() ELSE NULL END AS linked_at,
			?::BIGINT AS ingest_run_id
		FROM candidate_rows
		WHERE candidate_rank=1
	`, providerSource, providerFilingLinkerV1, ingestRunID); err != nil {
		return result, fmt.Errorf("build provider-filing link stage: %w", err)
	}

	rows, err := conn.QueryContext(ctx, `
		SELECT status, count(*) FROM temp.main.`+providerFilingStage+` GROUP BY status
	`)
	if err != nil {
		return result, fmt.Errorf("summarize provider-filing stage: %w", err)
	}
	for rows.Next() {
		var status string
		var count int
		if err := rows.Scan(&status, &count); err != nil {
			rows.Close()
			return result, fmt.Errorf("scan provider-filing summary: %w", err)
		}
		result.Records += count
		switch status {
		case ProviderFilingLinked:
			result.Linked = count
		case ProviderFilingPending:
			result.Pending = count
		case ProviderFilingAmbiguous:
			result.Ambiguous = count
		default:
			rows.Close()
			return result, fmt.Errorf("unknown provider-filing status %q", status)
		}
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return result, fmt.Errorf("iterate provider-filing summary: %w", err)
	}
	rows.Close()

	if _, err := conn.ExecContext(ctx, `
		INSERT INTO fundamental.provider_filing_link (
			provider_source, provider_revision_key, provider_artifact_id,
			provider_code, report_period, instrument_id, filing_id,
			status, candidate_count, link_method, reason, linker_version,
			linked_at, ingest_run_id
		)
		SELECT
			provider_source, provider_revision_key, provider_artifact_id,
			provider_code, report_period, instrument_id, filing_id,
			status, candidate_count, link_method, reason, linker_version,
			linked_at, ingest_run_id
		FROM temp.main.`+providerFilingStage+`
		ON CONFLICT(provider_source, provider_revision_key, provider_code) DO UPDATE SET
			provider_artifact_id=excluded.provider_artifact_id,
			report_period=excluded.report_period,
			instrument_id=excluded.instrument_id,
			filing_id=excluded.filing_id,
			status=excluded.status,
			candidate_count=excluded.candidate_count,
			link_method=excluded.link_method,
			reason=excluded.reason,
			linker_version=excluded.linker_version,
			linked_at=excluded.linked_at,
			ingest_run_id=excluded.ingest_run_id,
			updated_at=now()
	`); err != nil {
		return result, fmt.Errorf("merge provider-filing links: %w", err)
	}

	if err := conn.QueryRowContext(ctx, `
		WITH deleted AS (
			DELETE FROM fundamental.provider_filing_link l
			WHERE l.provider_source=?
			  AND NOT EXISTS (
				SELECT 1 FROM fundamental.provider_fact pf
				WHERE pf.source=l.provider_source
				  AND pf.revision_key=l.provider_revision_key
				  AND pf.provider_code=l.provider_code
			  )
			RETURNING 1
		)
		SELECT count(*) FROM deleted
	`, providerSource).Scan(&result.Removed); err != nil {
		return result, fmt.Errorf("remove stale provider-filing links: %w", err)
	}
	if _, err := conn.ExecContext(ctx, `DROP TABLE temp.main.`+providerFilingStage); err != nil {
		return result, fmt.Errorf("drop provider-filing stage: %w", err)
	}
	if _, err := conn.ExecContext(ctx, `COMMIT`); err != nil {
		return result, fmt.Errorf("commit provider-filing links: %w", err)
	}
	committed = true
	return result, nil
}
