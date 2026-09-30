package duckdb

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

// ExportFinancialSQLiteRows exports one read transaction, never one process per
// company or period. Only the requested standard columns are decoded from wide
// storage; the receiving native model defines its own target columns.
func ExportFinancialSQLiteRows(ctx context.Context, db *sql.DB, dir string, codes, fields []string, from, end, asof time.Time) error {
	if err := requireStandardFinancialSchema(ctx, db); err != nil {
		return err
	}
	if len(fields) == 0 || from.IsZero() || end.IsZero() || from.After(end) || asof.Before(end) {
		return fmt.Errorf("fields, history window and information cutoff required")
	}
	known, err := LoadSnapshotFields(ctx, db)
	if err != nil {
		return err
	}
	catalog := map[string]SnapshotField{}
	for _, f := range known {
		catalog[f.Name] = f
	}
	names := []string{}
	seen := map[string]bool{}
	for _, name := range fields {
		if _, ok := catalog[name]; !ok || seen[name] {
			return fmt.Errorf("unknown/duplicate standard field %q", name)
		}
		seen[name] = true
		names = append(names, `"`+name+`"`)
	}
	quotedCodes := []string{}
	for _, code := range codes {
		if !sixDigitCode.MatchString(code) {
			return fmt.Errorf("invalid security code")
		}
		quotedCodes = append(quotedCodes, duckdbStringLiteral(code))
	}
	if err = os.Mkdir(dir, 0700); err != nil {
		return err
	}
	root, err := FinancialArchiveRoot(ctx, db)
	if err != nil {
		return err
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	cutoff := duckdbStringLiteral(asof.UTC().Format(time.RFC3339Nano)) + `::TIMESTAMPTZ`
	start := duckdbStringLiteral(from.Format("2006-01-02")) + `::DATE`
	finish := duckdbStringLiteral(end.Format("2006-01-02")) + `::DATE`
	day := duckdbStringLiteral(asof.In(time.FixedZone("China", 8*3600)).Format("2006-01-02")) + `::DATE`
	quoteEnd := duckdbStringLiteral(domain.CompletedMarketDate(asof).Format("2006-01-02")) + `::DATE`
	codeFilter := ""
	market := `i.exchange_mic IN ('XSHG','XSHE')`
	if len(codes) > 0 {
		codeFilter = ` HAVING bool_or(right(d.identifier_value,6) IN (` + strings.Join(quotedCodes, ",") + `))`
		market = `i.exchange_mic IN ('XSHG','XSHE','XBSE')`
	}
	universe := `SELECT i.instrument_id,i.name,i.exchange_mic,list(DISTINCT d.identifier_value ORDER BY d.identifier_value) FILTER(WHERE d.identifier_value IS NOT NULL) AS symbols,count(DISTINCT d.identifier_value) AS symbol_count,count(d.identifier_value) AS identifier_count
 FROM core.instrument i LEFT JOIN core.instrument_identifier d ON d.instrument_id=i.instrument_id AND d.provider='tdx' AND d.identifier_type='symbol' AND (d.valid_from IS NULL OR d.valid_from<=` + day + `) AND (d.valid_to IS NULL OR d.valid_to>` + day + `)
 WHERE i.instrument_type='equity' AND i.currency='CNY' AND ` + market + ` AND (i.list_date IS NULL OR i.list_date<=` + day + `) AND (i.delist_date IS NULL OR i.delist_date>` + day + `) AND (i.status='active' OR i.delist_date IS NOT NULL)
 GROUP BY i.instrument_id,i.name,i.exchange_mic` + codeFilter
	if _, err = tx.ExecContext(ctx, `CREATE TEMP TABLE _sqlite_universe AS `+universe); err != nil {
		return err
	}
	copyQuery := func(name, query string) error {
		_, e := tx.ExecContext(ctx, `COPY (`+query+`) TO `+duckdbStringLiteral(filepath.Join(dir, name))+` (FORMAT JSON,ARRAY false)`)
		return e
	}
	if err = copyQuery("companies.jsonl", `SELECT u.*,q.quote FROM _sqlite_universe u LEFT JOIN (
 SELECT o.instrument_id,struct_pack(close:=CAST(o.close AS VARCHAR),trade_date:=CAST(o.trade_date AS VARCHAR),
 observation_id:=o.observation_id,ingest_run_id:=o.ingest_run_id,recorded_at:=CAST(o.recorded_at AS VARCHAR),
 acquisition_started_at:=CAST(r.started_at AS VARCHAR),run_finished_at:=CAST(r.finished_at AS VARCHAR),
 adjustment:='unadjusted',source:='tdx') AS quote
 FROM market.daily_observation o JOIN meta.ingest_run r USING(ingest_run_id)
 WHERE o.instrument_id IN(SELECT instrument_id FROM _sqlite_universe) AND o.source='tdx' AND r.source='tdx'
 AND r.dataset IN ('daily_ohlcv','valuation_quote_window') AND r.status IN ('completed','partial') AND o.close>0
 AND o.trade_date BETWEEN `+quoteEnd+`-INTERVAL 14 DAY AND `+quoteEnd+`
 AND r.started_at>=((o.trade_date::TIMESTAMP+INTERVAL 15 HOUR) AT TIME ZONE 'Asia/Shanghai')
 AND o.recorded_at<=`+cutoff+` AND r.finished_at<=`+cutoff+`
 QUALIFY row_number() OVER(PARTITION BY o.instrument_id ORDER BY o.trade_date DESC,o.recorded_at DESC,o.observation_id DESC)=1
 ) q USING(instrument_id) ORDER BY u.instrument_id`); err != nil {
		return err
	}
	// Filter issuer identities before wide reads; rank versions before code checks.
	values := `WITH headers AS MATERIALIZED (
 SELECT r.source_record_id,r.instrument_id,s.source_filing_id,r.report_period,s.announcement_time,s.announcement_source,r.provider_code,a.sha256 AS artifact_sha256,a.fetched_at AS source_observed_at
 FROM fundamental.source_record r JOIN meta.artifact a USING(artifact_id) LEFT JOIN fundamental.statement_snapshot s USING(source_record_id)
 WHERE r.instrument_id IN(SELECT instrument_id FROM _sqlite_universe) AND r.report_period BETWEEN ` + start + ` AND ` + finish + `
 QUALIFY row_number() OVER(PARTITION BY r.instrument_id,r.report_period ORDER BY a.fetched_at DESC,a.artifact_id DESC,r.source_record_id DESC)=1
 ), selected AS (SELECT h.*,` + "w." + strings.Join(names, ",w.") + ` FROM headers h LEFT JOIN fundamental.statement_snapshot w USING(source_record_id)),cells AS (
 UNPIVOT selected ON ` + strings.Join(names, ",") + ` INTO NAME field VALUE value
 )
 SELECT c.instrument_id,c.provider_code AS code,CAST(c.report_period AS VARCHAR) AS period,c.field,c.field AS canonical_field,CAST(c.value AS VARCHAR) AS value,m.unit,'tdx' AS source,'provider_default' AS statement_scope,
 CASE WHEN m.period_basis IN('instant','opening_instant') THEN m.period_basis WHEN m.period_basis='quarter' THEN 'Q'||CAST(quarter(c.report_period) AS VARCHAR) WHEN m.period_basis='ttm' THEN 'TTM' WHEN month(c.report_period)=3 THEN 'Q1' WHEN month(c.report_period)=6 THEN 'H1' WHEN month(c.report_period)=9 THEN '9M' ELSE 'FY' END AS period_type,
 c.source_record_id*8192+CAST(substr(m.provider_field,3) AS BIGINT) AS fact_id,c.source_record_id,c.source_filing_id,CAST(c.announcement_time AS VARCHAR) AS available_at,c.announcement_source,'current_standard_snapshot_not_pit' AS financial_time_basis,CAST(c.source_observed_at AS VARCHAR) AS source_observed_at,c.artifact_sha256
 FROM cells c JOIN fundamental.statement_field m ON m.source='tdx' AND m.canonical_field=c.field AND (m.valid_from IS NULL OR m.valid_from<=c.report_period) AND (m.valid_to IS NULL OR c.report_period<m.valid_to)
 ORDER BY c.provider_code,c.instrument_id,c.report_period,c.field`
	if err = copyQuery("facts.jsonl", values); err != nil {
		return err
	}
	if err = copyQuery("conflicts.jsonl", `SELECT provider_code AS code,CAST(report_period AS VARCHAR) AS period,reason,artifact_sha256 FROM fundamental.provider_conflicts_asof(current_timestamp) WHERE report_period BETWEEN `+start+` AND `+finish); err != nil {
		return err
	}
	if err = exportReviewedSourceZeros(ctx, tx, root, dir, fields, from, end, asof); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, `DROP TABLE _sqlite_universe`); err != nil {
		return err
	}
	return tx.Commit()
}
