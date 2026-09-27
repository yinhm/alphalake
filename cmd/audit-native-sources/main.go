// audit-native-sources 核对原生缺口的既有源记录；不写标准事实、不推断缺失为零。
package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	financial "github.com/yinhm/alphalake/internal/source/tdx/financial"
	store "github.com/yinhm/alphalake/internal/store/duckdb"
)

type gap struct {
	Field  string `json:"field"`
	Series string `json:"series"`
	Period string `json:"period"`
}
type company struct {
	ID    int64  `json:"instrument_id"`
	Code  string `json:"code"`
	Scope string `json:"scope"`
	Gaps  []gap  `json:"gaps"`
}
type coverage struct {
	Contract  string    `json:"contract_version"`
	Hash      string    `json:"source_database_sha256"`
	Period    string    `json:"report_period"`
	AsOf      string    `json:"information_as_of"`
	Companies []company `json:"companies"`
}
type request struct {
	ID     int64  `json:"id"`
	Code   string `json:"code"`
	Period string `json:"period"`
}

func digest(path string) (string, error) {
	f, e := os.Open(path)
	if e != nil {
		return "", e
	}
	defer f.Close()
	h := sha256.New()
	if _, e = io.Copy(h, f); e != nil {
		return "", e
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}
func requests(c coverage) ([]request, error) {
	if c.Contract != "alphalake-native-coverage-v1" {
		return nil, fmt.Errorf("unsupported coverage contract")
	}
	if _, e := time.Parse("2006-01-02", c.Period); e != nil {
		return nil, e
	}
	if _, e := time.Parse(time.RFC3339Nano, c.AsOf); e != nil {
		return nil, e
	}
	seen := map[int64]bool{}
	out := []request{}
	for _, co := range c.Companies {
		if co.ID <= 0 || seen[co.ID] || len(co.Code) != 6 || strings.Trim(co.Code, "0123456789") != "" {
			return nil, fmt.Errorf("invalid/duplicate identity")
		}
		seen[co.ID] = true
		if co.Scope != "nonfinancial_by_reference" {
			continue
		}
		periods := map[string]bool{}
		for _, g := range co.Gaps {
			if g.Field != "r_and_d_expense" || g.Series != "annual" {
				continue
			}
			if _, e := time.Parse("2006-01-02", g.Period); e != nil {
				return nil, e
			}
			if !periods[g.Period] {
				out = append(out, request{co.ID, co.Code, g.Period})
				periods[g.Period] = true
			}
		}
	}
	return out, nil
}
func run() error {
	database := flag.String("database", "", "主库路径")
	input := flag.String("coverage", "", "同库原生覆盖报告")
	output := flag.String("output", "", "新建JSON报告")
	flag.Parse()
	if *database == "" || *input == "" || *output == "" || flag.NArg() != 0 {
		return fmt.Errorf("require --database --coverage --output")
	}
	if _, e := os.Stat(*output); !os.IsNotExist(e) {
		return fmt.Errorf("output exists or cannot be checked")
	}
	raw, e := os.ReadFile(*input)
	if e != nil {
		return e
	}
	inputSum := sha256.Sum256(raw)
	var c coverage
	if e = json.Unmarshal(raw, &c); e != nil {
		return e
	}
	req, e := requests(c)
	if e != nil {
		return e
	}
	before, e := digest(*database)
	if e != nil {
		return e
	}
	if before != c.Hash {
		return fmt.Errorf("coverage source hash mismatch")
	}
	ctx := context.Background()
	db, e := store.OpenReadOnly(ctx, *database)
	if e != nil {
		return e
	}
	defer db.Close()
	db.SetMaxOpenConns(1)
	root, e := store.FinancialArchiveRoot(ctx, db)
	if e != nil {
		return e
	}
	encoded, e := json.Marshal(req)
	if e != nil {
		return e
	}
	_, e = db.ExecContext(ctx, `CREATE TEMP TABLE requested AS SELECT (value->>'id')::BIGINT id,value->>'code' code,(value->>'period')::DATE period FROM json_each(?)`, string(encoded))
	if e != nil {
		return e
	}
	encoded, e = json.Marshal(c.Companies)
	if e != nil {
		return e
	}
	_, e = db.ExecContext(ctx, `CREATE TEMP TABLE companies AS SELECT (value->>'instrument_id')::BIGINT id,value->>'code' code FROM json_each(?) WHERE value->>'scope'='nonfinancial_by_reference'`, string(encoded))
	if e != nil {
		return e
	}
	read := func(query string, args ...any) ([]json.RawMessage, error) {
		rows, e := db.QueryContext(ctx, `SELECT CAST(to_json(t) AS VARCHAR) FROM (`+query+`) t`, args...)
		if e != nil {
			return nil, e
		}
		defer rows.Close()
		out := []json.RawMessage{}
		for rows.Next() {
			var s string
			if e = rows.Scan(&s); e != nil {
				return nil, e
			}
			out = append(out, json.RawMessage(s))
		}
		return out, rows.Err()
	}
	market, e := read(`SELECT c.id instrument_id,c.code,count(o.observation_id) observations,
 (SELECT count(*) FROM market.ohlcv_daily b WHERE b.instrument_id=c.id AND b.source='tdx') canonical_bars,
 count(o.observation_id) FILTER(WHERE o.trade_date BETWEEN CAST(? AS DATE)-INTERVAL 14 DAY AND CAST(? AS DATE)) period_window_observations,
 min(o.trade_date) first_trade_date,max(o.trade_date) last_trade_date,
 count(o.observation_id) FILTER(WHERE o.recorded_at<=CAST(? AS TIMESTAMPTZ)) observations_at_cutoff
 FROM companies c LEFT JOIN market.daily_observation o ON o.instrument_id=c.id AND o.source='tdx' GROUP BY c.id,c.code ORDER BY c.id`, c.Period, c.Period, c.AsOf)
	if e != nil {
		return e
	}
	links, e := read(`SELECT q.id instrument_id,q.code,CAST(q.period AS VARCHAR) period,r.source_record_id,r.artifact_id,r.source_row,r.market_marker,
 r.instrument_id source_instrument_id,r.field_count,l.status link_status,l.reason link_reason,l.filing_id,
 f.announcement_time,f.resolution_status filing_resolution,s.research_and_development_expense standard_value,
 (SELECT list(j.rule_code ORDER BY j.rule_code) FROM fundamental.statement_rejection j WHERE j.source_record_id=r.source_record_id AND list_contains(j.fields,'research_and_development_expense')) rejection_rules,
 (SELECT count(*) FROM fundamental.filing f2 WHERE f2.instrument_id=q.id AND f2.report_period=q.period AND f2.filing_type='annual') annual_catalogue_rows
 FROM requested q LEFT JOIN fundamental.source_record r ON r.provider_code=q.code AND r.report_period=q.period
 LEFT JOIN fundamental.provider_filing_link l ON l.provider_artifact_id=r.artifact_id AND l.provider_code=r.provider_code
 LEFT JOIN fundamental.filing f ON f.filing_id=l.filing_id
 LEFT JOIN fundamental.statement_snapshot s ON s.source_record_id=r.source_record_id
 ORDER BY q.id,q.period,r.source_record_id`)
	if e != nil {
		return e
	}
	type locator struct {
		ID       int64  `json:"source_record_id"`
		Artifact int64  `json:"artifact_id"`
		Row      int    `json:"source_row"`
		Marker   byte   `json:"market_marker"`
		Fields   int    `json:"field_count"`
		Code     string `json:"code"`
		Period   string `json:"period"`
	}
	byArtifact := map[int64][]locator{}
	seen := map[int64]bool{}
	for _, row := range links {
		var l locator
		if e = json.Unmarshal(row, &l); e != nil {
			return e
		}
		if l.ID != 0 && !seen[l.ID] {
			seen[l.ID] = true
			byArtifact[l.Artifact] = append(byArtifact[l.Artifact], l)
		}
	}
	catalog, e := financial.FieldCatalog()
	if e != nil {
		return e
	}
	var field financial.FieldDefinition
	matches := 0
	for _, f := range catalog {
		if f.Name == "research_and_development_expense" {
			field = f
			matches++
		}
	}
	if matches != 1 {
		return fmt.Errorf("research field is not unique")
	}
	source := map[int64]any{}
	// 一包一次读取，数值只留在独立来源证据，不物化为标准事实。
	for artifact, locators := range byArtifact {
		var path, name, hash string
		var size int64
		if e = db.QueryRowContext(ctx, `SELECT local_path,source_locator,sha256,content_length FROM meta.artifact WHERE artifact_id=?`, artifact).Scan(&path, &name, &hash, &size); e != nil {
			return e
		}
		pkg, e := store.ReadFinancialArchive(root, path, name, hash, size)
		if e != nil {
			return fmt.Errorf("artifact %d: %w", artifact, e)
		}

		for _, l := range locators {
			if l.Row < 1 || l.Row > len(pkg.Records) {
				return fmt.Errorf("source row outside archive")
			}
			r := pkg.Records[l.Row-1]
			if r.Code != l.Code || r.ReportPeriod.Format("2006-01-02") != l.Period || r.MarketMarker != l.Marker || len(r.Fields) != l.Fields {
				return fmt.Errorf("source row identity/period mismatch")
			}
			if len(r.Fields) < field.Index {
				source[l.ID] = map[string]any{"state": "field_absent_from_record", "artifact_sha256": hash}
				continue
			}
			source[l.ID] = map[string]any{"observation": financial.DecodeSourceValue(field, r.Fields[field.Index-1]), "artifact_sha256": hash}
		}
	}
	after, e := digest(*database)
	if e != nil {
		return e
	}
	if after != before {
		return fmt.Errorf("source changed during audit")
	}
	report := map[string]any{"contract_version": "alphalake-native-source-audit-v1", "source_database_sha256": before, "coverage_sha256": hex.EncodeToString(inputSum[:]), "report_period": c.Period, "information_as_of": c.AsOf, "research_requested_cells": len(req), "research_links": links, "source_evidence": source, "market_observations": market, "boundary": "只核对当前主库及已引用归档；无记录不等于上游无数据。源数值不是已审核事实；行情窗口计数不代替原生完整准入。金融及分类待审不在本次根因分母。"}
	f, e := os.OpenFile(*output, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if e != nil {
		return e
	}
	defer f.Close()
	enc := json.NewEncoder(f)
	enc.SetIndent("", "  ")
	return enc.Encode(report)
}
func main() {
	if e := run(); e != nil {
		fmt.Fprintln(os.Stderr, e)
		os.Exit(1)
	}
}
