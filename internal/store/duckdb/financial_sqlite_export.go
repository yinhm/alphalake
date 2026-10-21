package duckdb

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

// FinancialExportSecurity binds an already reviewed publication identity.
type FinancialExportSecurity struct {
	InstrumentID int64  `json:"instrument_id"`
	Ticker       string `json:"ticker"`
	Name         string `json:"name"`
}

// ExportFinancialSQLiteRows exports one read transaction, never one process per
// company or period. Only the requested standard columns are decoded from wide
// storage; the receiving native model defines its own target columns.
func ExportFinancialSQLiteRows(ctx context.Context, db *sql.DB, dir string, codes, fields []string, from, end, asof time.Time, fieldPeriods map[string][]time.Time, scope []FinancialExportSecurity) error {
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
	periodSQL := map[string]string{}
	allPeriods := map[string]bool{}
	for field, periods := range fieldPeriods {
		if !seen[field] || len(periods) == 0 {
			return fmt.Errorf("unknown field or empty report periods: %s", field)
		}
		quoted := []string{}
		for _, period := range periods {
			if period.Before(from) || period.After(end) || period.Month()%3 != 0 || period.AddDate(0, 0, 1).Day() != 1 {
				return fmt.Errorf("invalid requested report period for %s", field)
			}
			value := duckdbStringLiteral(period.Format("2006-01-02")) + "::DATE"
			quoted = append(quoted, value)
			allPeriods[value] = true
		}
		periodSQL[field] = strings.Join(quoted, ",")
	}
	if len(fieldPeriods) != 0 && len(fieldPeriods) != len(fields) {
		return fmt.Errorf("report periods required for every selected field")
	}
	periodFilter := ""
	if len(allPeriods) > 0 {
		values := []string{}
		for value := range allPeriods {
			values = append(values, value)
		}
		sort.Strings(values)
		periodFilter = " AND r.report_period IN (" + strings.Join(values, ",") + ")"
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
	if len(scope) > 0 {
		if len(codes) > 0 {
			return fmt.Errorf("choose codes or frozen publication scope")
		}
		rows, ids, tickers := []string{}, map[int64]bool{}, map[string]bool{}
		for _, security := range scope {
			parts := strings.Split(security.Ticker, ":")
			if len(parts) != 2 || (parts[0] != "SHSE" && parts[0] != "SZSE") || !sixDigitCode.MatchString(parts[1]) || security.InstrumentID <= 0 || security.Name == "" || ids[security.InstrumentID] || tickers[security.Ticker] {
				return fmt.Errorf("invalid/duplicate frozen security identity")
			}
			ids[security.InstrumentID], tickers[security.Ticker] = true, true
			symbol, mic := "sh"+parts[1], "XSHG"
			if parts[0] == "SZSE" {
				symbol, mic = "sz"+parts[1], "XSHE"
			}
			rows = append(rows, fmt.Sprintf("(%d,%s,%s,%s)", security.InstrumentID, duckdbStringLiteral(symbol), duckdbStringLiteral(mic), duckdbStringLiteral(security.Name)))
		}
		if _, err = tx.ExecContext(ctx, `CREATE TEMP TABLE _sqlite_scope AS SELECT * FROM (VALUES `+strings.Join(rows, ",")+`) s(instrument_id,symbol,mic,name)`); err != nil {
			return err
		}
		// Source absence closes a provider identifier, not the canonical company.
		// A reused/wrong identity stays in the publication with no financial values.
		universe = `WITH checked AS (
 SELECT s.*,i.name AS current_name,
 i.instrument_id IS NULL OR i.instrument_type<>'equity' OR i.currency<>'CNY' OR i.exchange_mic<>s.mic
 OR (i.list_date IS NOT NULL AND i.list_date>` + day + `)
 OR (i.delist_date IS NOT NULL AND i.delist_date<=` + day + `)
 OR (i.status<>'active' AND i.delist_date IS NULL)
 OR NOT EXISTS(SELECT 1 FROM core.instrument_identifier d WHERE d.instrument_id=s.instrument_id AND d.provider='tdx' AND d.identifier_type='symbol' AND d.identifier_value=s.symbol AND (d.valid_from IS NULL OR d.valid_from<=` + day + `))
 OR EXISTS(SELECT 1 FROM core.instrument_identifier d WHERE d.instrument_id<>s.instrument_id AND d.provider='tdx' AND d.identifier_type='symbol' AND d.identifier_value=s.symbol AND (d.valid_from IS NULL OR d.valid_from<=` + day + `) AND (d.valid_to IS NULL OR d.valid_to>` + day + `)) AS blocked,
 (SELECT count(*) FROM core.instrument_identifier d WHERE d.instrument_id=s.instrument_id AND d.provider='tdx' AND d.identifier_type='symbol' AND d.identifier_value=s.symbol AND (d.valid_from IS NULL OR d.valid_from<=` + day + `) AND (d.valid_to IS NULL OR d.valid_to>` + day + `)) AS active_identifiers
 FROM _sqlite_scope s LEFT JOIN core.instrument i USING(instrument_id))
 SELECT instrument_id,CASE WHEN blocked OR active_identifiers>1 THEN name ELSE coalesce(current_name,name) END AS name,mic AS exchange_mic,[symbol] AS symbols,
 1 AS symbol_count,1 AS identifier_count,true AS scope_bound,
 CASE WHEN blocked OR active_identifiers>1 THEN 'blocked_security_identity' WHEN active_identifiers=0 THEN 'inactive_source_identifier' ELSE 'active' END AS source_identity_status,
 CASE WHEN blocked OR active_identifiers>1 THEN 'blocked_security_identity' ELSE NULL END AS financial_status
 FROM checked`
	} else {
		universe = `SELECT *,false AS scope_bound,'active' AS source_identity_status,NULL::VARCHAR AS financial_status FROM (` + universe + `)`
	}
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
 WHERE o.instrument_id IN(SELECT instrument_id FROM _sqlite_universe WHERE financial_status IS NULL) AND o.source='tdx' AND r.source='tdx'
 AND r.dataset IN ('daily_ohlcv','valuation_quote_window') AND r.status IN ('completed','partial') AND o.close>0
 AND o.trade_date BETWEEN `+quoteEnd+`-INTERVAL 14 DAY AND `+quoteEnd+`
 AND r.started_at>=((o.trade_date::TIMESTAMP+INTERVAL 15 HOUR) AT TIME ZONE 'Asia/Shanghai')
 AND o.recorded_at<=`+cutoff+` AND r.finished_at<=`+cutoff+`
 QUALIFY row_number() OVER(PARTITION BY o.instrument_id ORDER BY o.trade_date DESC,o.recorded_at DESC,o.observation_id DESC)=1
 ) q USING(instrument_id) ORDER BY u.instrument_id`); err != nil {
		return err
	}
	memberships, reference, err := valuationIndustriesAsOf(ctx, tx, asof)
	if err != nil {
		return err
	}
	scopeFile, err := os.Create(filepath.Join(dir, "industries.jsonl"))
	if err != nil {
		return err
	}
	encoder := json.NewEncoder(scopeFile)
	ids := make([]int64, 0, len(memberships))
	for id := range memberships {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	for _, id := range ids {
		members := memberships[id]
		if err = encoder.Encode(map[string]any{"instrument_id": id, "industry_memberships": members}); err != nil {
			scopeFile.Close()
			return err
		}
	}
	if err = scopeFile.Close(); err != nil {
		return err
	}
	referenceJSON, err := json.Marshal(reference)
	if err != nil {
		return err
	}
	if err = os.WriteFile(filepath.Join(dir, "industry-reference.json"), referenceJSON, 0600); err != nil {
		return err
	}
	// Sort narrow report rows before JSON export. Expanding shared lineage for
	// every field before sorting multiplies memory and intermediate file size.
	fieldNames, values := []string{}, []string{}
	for i, name := range fields {
		fieldNames = append(fieldNames, duckdbStringLiteral(name))
		expression := `w.` + names[i]
		if periods, ok := periodSQL[name]; ok {
			expression = `CASE WHEN h.report_period IN (` + periods + `) THEN ` + expression + ` END`
		}
		values = append(values, `CAST(`+expression+` AS VARCHAR) AS `+names[i])
	}
	if err = copyQuery("fields.jsonl", `SELECT canonical_field AS field,provider_field,unit,period_basis,
 CAST(valid_from AS VARCHAR) AS valid_from,CAST(valid_to AS VARCHAR) AS valid_to
 FROM fundamental.statement_field WHERE source='tdx' AND canonical_field IN (`+strings.Join(fieldNames, ",")+`)
 ORDER BY canonical_field,valid_from`); err != nil {
		return err
	}
	records := `WITH headers AS MATERIALIZED (
 SELECT r.source_record_id,r.instrument_id,s.source_filing_id,r.report_period,s.announcement_time,s.announcement_source,r.provider_code,a.sha256 AS artifact_sha256,a.fetched_at AS source_observed_at
 FROM fundamental.source_record r JOIN meta.artifact a USING(artifact_id) LEFT JOIN fundamental.statement_snapshot s USING(source_record_id)
 WHERE r.instrument_id IN(SELECT instrument_id FROM _sqlite_universe WHERE financial_status IS NULL) AND r.report_period BETWEEN ` + start + ` AND ` + finish + periodFilter + `
 QUALIFY row_number() OVER(PARTITION BY r.instrument_id,r.report_period ORDER BY a.fetched_at DESC,a.artifact_id DESC,r.source_record_id DESC)=1
 )
 SELECT h.instrument_id,h.provider_code AS code,CAST(h.report_period AS VARCHAR) AS period,
 h.source_record_id,h.source_filing_id,CAST(h.announcement_time AS VARCHAR) AS available_at,h.announcement_source,
 CAST(h.source_observed_at AS VARCHAR) AS source_observed_at,h.artifact_sha256,` + strings.Join(values, ",") + `
 FROM headers h LEFT JOIN fundamental.statement_snapshot w USING(source_record_id)
 ORDER BY h.provider_code,h.instrument_id,h.report_period`
	if err = copyQuery("records.jsonl", records); err != nil {
		return err
	}
	if err = copyQuery("conflicts.jsonl", `SELECT provider_code AS code,CAST(report_period AS VARCHAR) AS period,reason,artifact_sha256 FROM fundamental.provider_conflicts_asof(current_timestamp) WHERE provider_code IN (SELECT right(unnest(symbols),6) FROM _sqlite_universe) AND report_period BETWEEN `+start+` AND `+finish+strings.ReplaceAll(periodFilter, "r.report_period", "report_period")+` ORDER BY provider_code,report_period,artifact_sha256,reason`); err != nil {
		return err
	}
	if err = exportReviewedSourceZeros(ctx, tx, root, dir, fields, from, end, asof); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, `DROP TABLE _sqlite_universe`); err != nil {
		return err
	}
	if len(scope) > 0 {
		if _, err = tx.ExecContext(ctx, `DROP TABLE _sqlite_scope`); err != nil {
			return err
		}
	}
	return tx.Commit()
}
