package duckdb

import (
	"context"
	"database/sql"
	"fmt"
	"strings"
)

// InstallSnapshotQueries expands only the requested securities/report window.
// Raw observations retain all codes of candidate issuers so as-of ranking can
// precede the final code filter. Wide columns are the sole stored numeric data.
// Materialize only narrow headers before fetching numeric columns by the existing
// record primary key; otherwise filtering can still decode every wide column.
func InstallSnapshotQueries(ctx context.Context, db *sql.DB, fields []SnapshotField) error {
	return installSnapshotQueries(ctx, db, fields)
}

func installSnapshotQueries(ctx context.Context, db snapshotDB, fields []SnapshotField) error {
	query, err := snapshotObservationQueries(fields)
	if err != nil {
		return err
	}
	_, err = db.ExecContext(ctx, `CREATE OR REPLACE TABLE fundamental.statement_field AS
 SELECT m.* FROM fundamental.provider_field m JOIN fundamental.field f
 ON f.canonical_field=m.canonical_field AND f.unit=m.unit AND f.value_kind=m.value_kind AND f.period_basis=m.period_basis;`+query)
	if err != nil {
		return err
	}
	_, err = db.ExecContext(ctx, financialQueriesSQL)
	return err
}

// snapshotObservationQueries shares ranking and lineage for full and projected reads.
func snapshotObservationQueries(fields []SnapshotField) (string, error) {
	columns := make([]string, len(fields))
	wideColumns := make([]string, len(fields))
	for i, f := range fields {
		if !standardSnapshotName.MatchString(f.Name) {
			return "", fmt.Errorf("invalid standard field name")
		}
		columns[i] = `"` + f.Name + `"`
		wideColumns[i] = "w." + columns[i]
	}
	return `
 CREATE OR REPLACE MACRO fundamental.financial_observations(security_code,from_period,to_period,as_of_time,min_instrument_id := NULL,max_instrument_id := NULL) AS TABLE (
 WITH headers AS MATERIALIZED (
 SELECT s.source_record_id,s.instrument_id,s.source_filing_id,s.report_period,greatest(s.announcement_time,correction.announcement_time) AS announcement_time,CASE WHEN correction.announcement_time>s.announcement_time OR (s.announcement_time IS NULL AND correction.announcement_time IS NOT NULL) THEN 'cninfo_correction' ELSE s.announcement_source END AS announcement_source,s.ingest_run_id,r.provider_code,r.artifact_id,r.source_row,a.source AS primary_source,a.sha256 AS revision_key
 FROM fundamental.statement_snapshot s JOIN fundamental.source_record r USING(source_record_id)
 JOIN meta.artifact a USING(artifact_id)
 LEFT JOIN fundamental.filing correction ON correction.filing_id=s.source_filing_id AND correction.is_correction
 AND correction.resolution_status='resolved' AND correction.instrument_id=s.instrument_id AND correction.report_period=s.report_period
 WHERE (min_instrument_id IS NULL OR s.instrument_id>=min_instrument_id) AND (max_instrument_id IS NULL OR s.instrument_id<=max_instrument_id)
 AND (security_code IS NULL OR s.instrument_id IN (
 SELECT candidate.instrument_id FROM fundamental.statement_snapshot candidate
 JOIN fundamental.source_record locator USING(source_record_id) WHERE locator.provider_code=security_code))
 AND (from_period IS NULL OR s.report_period>=CAST(from_period AS DATE))
 AND (to_period IS NULL OR s.report_period<=CAST(to_period AS DATE))
 AND (as_of_time IS NULL OR greatest(s.announcement_time,correction.announcement_time)<=CAST(as_of_time AS TIMESTAMPTZ))
 ), selected AS (
 SELECT h.*,` + strings.Join(wideColumns, ",") + `
 FROM headers h JOIN fundamental.statement_snapshot w USING(source_record_id)
 ), numeric_cells AS (
 UNPIVOT selected ON ` + strings.Join(columns, ",") + ` INTO NAME canonical_field VALUE value
 ), observations AS (
 SELECT c.* EXCLUDE(ingest_run_id),coalesce(l.ingest_run_id,c.ingest_run_id) AS ingest_run_id,m.unit,m.period_basis,m.value_kind,m.provider_field AS source_provider_field,m.value_multiplier,
 c.source_record_id*8192+CAST(substr(m.provider_field,3) AS BIGINT) AS fact_id,
 CASE WHEN m.period_basis IN ('instant','opening_instant') THEN m.period_basis
 WHEN m.period_basis='ttm' THEN 'TTM' WHEN m.period_basis='quarter' THEN 'Q'||CAST(quarter(c.report_period) AS VARCHAR)
 WHEN m.period_basis='ytd' AND month(c.report_period)=9 THEN '9M'
 WHEN month(c.report_period)=3 AND day(c.report_period)=31 THEN 'Q1'
 WHEN month(c.report_period)=6 AND day(c.report_period)=30 THEN 'H1'
 WHEN month(c.report_period)=9 AND day(c.report_period)=30 THEN 'Q3'
 WHEN month(c.report_period)=12 AND day(c.report_period)=31 THEN 'FY' ELSE 'unknown' END AS period_type,
 'provider_default' AS statement_scope,
 CASE WHEN m.value_kind IN ('monetary','per_share') THEN 'CNY' END AS currency,
 'tdx-float32-decimal-v4' AS normalization_rule,'standard-financial-v7' AS materializer_version
 FROM numeric_cells c LEFT JOIN fundamental.statement_field_run l ON l.source_record_id=c.source_record_id AND l.canonical_field=c.canonical_field
 JOIN fundamental.statement_field m ON m.source=c.primary_source AND m.canonical_field=c.canonical_field
 AND (m.valid_from IS NULL OR m.valid_from<=c.report_period) AND (m.valid_to IS NULL OR c.report_period<m.valid_to)
 ) SELECT * FROM observations o
 WHERE as_of_time IS NULL OR o.announcement_source!='cninfo'
 OR NOT EXISTS(SELECT 1 FROM fundamental.active_filing_coverage c WHERE c.filing_id=o.source_filing_id AND c.report_period=o.report_period)
 OR EXISTS(SELECT 1 FROM fundamental.active_filing_coverage c WHERE c.filing_id=o.source_filing_id AND c.report_period=o.report_period AND list_contains(c.fields,o.canonical_field))
 );
 CREATE OR REPLACE MACRO fundamental.financial_observations_asof(security_code,from_period,to_period,as_of_time,min_instrument_id := NULL,max_instrument_id := NULL) AS TABLE (
 SELECT * EXCLUDE(rank) FROM (
 SELECT *,row_number() OVER(PARTITION BY instrument_id,canonical_field,report_period ORDER BY announcement_time DESC,fact_id DESC) AS rank
 FROM fundamental.financial_observations(security_code,from_period,to_period,as_of_time,min_instrument_id := min_instrument_id,max_instrument_id := max_instrument_id)
 ) WHERE rank=1 AND (security_code IS NULL OR provider_code=security_code)
 );`, nil
}
