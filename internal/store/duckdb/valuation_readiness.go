package duckdb

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"strings"
	"time"
)

// ExportValuationReadiness 从本地证券主数据出发；无事实、无唯一代码也保留在分母。
// 这里只检查标准财务窗口，不把字段齐全等同于模型或市场数据就绪。
// ponytail: 行业仅保留最新观察，回溯早于该观察时保守不输出；历史行业回测需独立观察日志。
func ExportValuationReadiness(ctx context.Context, db *sql.DB, end, asof time.Time) (map[string]any, error) {
	if end.IsZero() || asof.IsZero() || end.After(asof) || end.AddDate(0, 0, 1).Day() != 1 || int(end.Month())%3 != 0 {
		return nil, errors.New("quarter-end period and later information cutoff required")
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	var raw sql.NullString
	err = tx.QueryRowContext(ctx, `WITH universe AS (
 SELECT i.instrument_id,i.name,i.exchange_mic,
 list(DISTINCT d.identifier_value ORDER BY d.identifier_value) FILTER (WHERE d.identifier_value IS NOT NULL) AS symbols,
 count(DISTINCT d.identifier_value) AS symbol_count,
 count(d.identifier_value) AS identifier_count
 FROM ref.instrument i LEFT JOIN ref.instrument_identifier d ON d.instrument_id=i.instrument_id
 AND d.provider='tdx' AND d.identifier_type='symbol'
 AND (d.valid_from IS NULL OR d.valid_from<=CAST(? AS DATE))
 AND (d.valid_to IS NULL OR d.valid_to>CAST(? AS DATE))
 WHERE i.instrument_type='equity' AND i.currency='CNY' AND i.exchange_mic IN ('XSHG','XSHE','XBSE')
 AND (i.list_date IS NULL OR i.list_date<=CAST(? AS DATE))
 AND (i.delist_date IS NULL OR i.delist_date>CAST(? AS DATE))
 AND (i.status='active' OR i.delist_date IS NOT NULL)
 GROUP BY i.instrument_id,i.name,i.exchange_mic
 ), latest AS (
 SELECT instrument_id,max(report_period) AS latest_report_period FROM fundamental.fact_asof(CAST(? AS TIMESTAMPTZ))
 WHERE primary_source='tdx' AND report_period<=CAST(? AS DATE) GROUP BY instrument_id
 ), windows AS (
 SELECT instrument_id,list(struct_pack(field:=source_provider_field,canonical_field:=canonical_field,
 value:=CAST(value AS VARCHAR),unit:=unit,scope:=statement_scope,status:=coverage_status,
 required_inputs:=required_inputs,available_inputs:=available_inputs,missing_periods:=missing_periods,
 source_fact_ids:=source_fact_ids) ORDER BY source_provider_field,provider_code,statement_scope) AS fields
 FROM fundamental.ttm_asof(CAST(? AS TIMESTAMPTZ),CAST(? AS DATE)) GROUP BY instrument_id
 ), industries AS (
 SELECT m.instrument_id,list(struct_pack(source:=t.source,taxonomy_code:=t.taxonomy_code,
 node_code:=n.source_node_code,node_name:=n.name,node_id:=n.node_id,
 observed_at:=CAST(m.last_observed_at AS VARCHAR),effective_from:=CAST(m.effective_from AS VARCHAR),
 ingest_run_id:=m.last_observed_run_id,run_finished_at:=CAST(r.finished_at AS VARCHAR))
 ORDER BY t.taxonomy_code,n.source_node_code) AS industry_memberships
 FROM classification.membership m JOIN classification.node n USING(node_id)
 JOIN classification.taxonomy t USING(taxonomy_id) JOIN meta.ingest_run r ON r.ingest_run_id=m.last_observed_run_id
 WHERE t.taxonomy_type='industry' AND m.source=t.source AND r.source=m.source
 AND r.dataset='classification_industry' AND r.status IN ('completed','partial')
 AND m.last_observed_at<=CAST(? AS TIMESTAMPTZ) AND r.finished_at<=CAST(? AS TIMESTAMPTZ)
 AND m.effective_from<=CAST(? AS DATE) AND (m.effective_to IS NULL OR m.effective_to>CAST(? AS DATE))
 GROUP BY m.instrument_id
 ) SELECT CAST(to_json(list(r ORDER BY instrument_id)) AS VARCHAR) FROM (
 SELECT u.*,CAST(l.latest_report_period AS VARCHAR) AS latest_report_period,w.fields,c.industry_memberships
 FROM universe u LEFT JOIN latest l USING(instrument_id) LEFT JOIN windows w USING(instrument_id) LEFT JOIN industries c USING(instrument_id)) r`,
		asof.In(time.FixedZone("China", 8*3600)).Format("2006-01-02"), asof.In(time.FixedZone("China", 8*3600)).Format("2006-01-02"),
		asof.In(time.FixedZone("China", 8*3600)).Format("2006-01-02"), asof.In(time.FixedZone("China", 8*3600)).Format("2006-01-02"), asof, end, asof, end, asof, asof, asof.In(time.FixedZone("China", 8*3600)).Format("2006-01-02"), asof.In(time.FixedZone("China", 8*3600)).Format("2006-01-02")).Scan(&raw)
	if err != nil {
		return nil, err
	}
	if !raw.Valid {
		raw.String = "[]"
	}
	var rows []map[string]any
	decoder := json.NewDecoder(strings.NewReader(raw.String))
	decoder.UseNumber()
	if err = decoder.Decode(&rows); err != nil {
		return nil, err
	}
	required := []string{"FN230", "FN86", "FN305", "FN306", "FN83", "FN82", "FN301", "FN238", "FN133", "FN41", "FN52", "FN55", "FN56", "FN439", "FN69"}
	counts := map[string]int{}
	gaps := map[string]int{}
	symbolOwners := map[string]int{}
	for _, r := range rows {
		if symbols, ok := r["symbols"].([]any); ok {
			for _, symbol := range symbols {
				symbolOwners[symbol.(string)]++
			}
		}
	}
	for _, r := range rows {
		available := map[string]int{}
		if fields, ok := r["fields"].([]any); ok {
			for _, v := range fields {
				f := v.(map[string]any)
				unit := "CNY"
				if f["field"] == "FN238" {
					unit = "share"
				}
				if f["status"] == "complete" && f["value"] != nil && f["scope"] == "provider_default" && f["unit"] == unit {
					available[f["field"].(string)]++
				}
			}
		}
		missing := []string{}
		for _, f := range required {
			if available[f] != 1 {
				missing = append(missing, f)
				gaps[f]++
			}
		}
		status := "financial_core_complete_requires_policy"
		if len(missing) > 0 {
			status = "blocked_financial_inputs"
		}
		if r["latest_report_period"] == nil {
			status = "blocked_no_standard_facts"
		}
		identityOK := r["symbol_count"] == json.Number("1") && r["identifier_count"] == json.Number("1")
		if identityOK {
			symbol := r["symbols"].([]any)[0].(string)
			identityOK = symbolOwners[symbol] == 1 && len(symbol) == 8 && sixDigitCode.MatchString(symbol[2:]) && map[string]string{"sh": "XSHG", "sz": "XSHE", "bj": "XBSE"}[symbol[:2]] == r["exchange_mic"]
		}
		if !identityOK {
			status = "blocked_security_identity"
		}
		r["financial_status"] = status
		r["missing_core_fields"] = missing
		r["valuation_status"] = "not_assessed_requires_model_policy_and_market_inputs"
		counts[status]++
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"contract_version": "alphalake-readiness-v1", "report_period": end.Format("2006-01-02"), "information_as_of": asof.UTC().Format(time.RFC3339Nano),
		"universe_scope":       "local_known_mainland_CNY_equities_not_verified_exchange_census_or_historical_master_snapshot",
		"required_core_fields": required, "universe_count": len(rows), "financial_status_counts": counts, "missing_core_field_counts": gaps, "companies": rows}, nil
}
