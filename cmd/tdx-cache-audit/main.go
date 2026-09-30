// tdx-cache-audit批量核对本地源位置，不写库、不批准标准事实或猜证券身份。
package main

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/json"
	"flag"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/ingest"
	"github.com/yinhm/alphalake/internal/source/tdx/financial"
	store "github.com/yinhm/alphalake/internal/store/duckdb"
)

type request struct {
	Code   string `json:"code"`
	Period string `json:"period"`
	Field  string `json:"field"`
}

func audit(cache string, requests []request, db *sql.DB) (map[string]any, error) {
	if filepath.Base(cache) != "tdx-cache" {
		return nil, fmt.Errorf("authoritative flat tdx-cache required")
	}
	catalog, err := financial.FieldCatalog()
	if err != nil {
		return nil, err
	}
	definitions := map[string][]financial.FieldDefinition{}
	for _, f := range catalog {
		if f.Name != "" {
			definitions[f.Name] = append(definitions[f.Name], f)
		}
	}
	groups := map[string][]request{}
	validCode := regexp.MustCompile(`^[0-9]{6}$`)
	for _, r := range requests {
		day, e := time.Parse("2006-01-02", r.Period)
		if e != nil || !validCode.MatchString(r.Code) || len(definitions[r.Field]) == 0 {
			return nil, fmt.Errorf("invalid standard source request: %+v", r)
		}
		groups["gpcw"+day.Format("20060102")+".zip"] = append(groups["gpcw"+day.Format("20060102")+".zip"], r)
	}
	manifest, err := os.ReadFile(filepath.Join(cache, "gpcw.txt"))
	if err != nil {
		return nil, err
	}
	entries, err := financial.ParseFileList(manifest)
	if err != nil {
		return nil, err
	}
	byName := map[string]financial.FileEntry{}
	for _, e := range entries {
		if _, exists := byName[e.Filename]; exists {
			return nil, fmt.Errorf("duplicate manifest filename")
		}
		byName[e.Filename] = e
	}
	names := []string{}
	for name := range groups {
		names = append(names, name)
	}
	sort.Strings(names)
	results := []map[string]any{}
	packages := []map[string]any{}
	for _, name := range names {
		entry, listed := byName[name]
		if !listed {
			return nil, fmt.Errorf("package absent from manifest: %s", name)
		}
		actual, raw, e := ingest.ReadFinancialCache(context.Background(), db, filepath.Dir(cache), entry)
		if e != nil && !os.IsNotExist(e) {
			return nil, e
		}
		packageStatus := "verified_local_manifest_not_upstream_checked"
		if os.IsNotExist(e) {
			packageStatus = "missing_local_package"
		} else if actual.MD5 != entry.MD5 || actual.Size != entry.Size {
			packageStatus = "verified_historical_receipt_manifest_differs"
		}
		sha := sha256.Sum256(raw)
		packages = append(packages, map[string]any{"file": name, "status": packageStatus, "sha256": fmt.Sprintf("%x", sha), "manifest_md5": entry.MD5})
		pkg := financial.Package{}
		if e == nil {
			pkg, err = financial.ParsePackage(name, raw)
			if err != nil {
				return nil, err
			}
		}
		// 一个包只解析一次；保留重复代码的所有行，不据此裁定证券或真值。
		rows := map[string][]int{}
		for i, row := range pkg.Records {
			rows[row.Code] = append(rows[row.Code], i)
		}
		for _, r := range groups[name] {
			matches := []map[string]any{}
			status := "absent_code_in_verified_local_package"
			if e != nil {
				status = packageStatus
			}
			for _, i := range rows[r.Code] {
				row := pkg.Records[i]
				for _, f := range definitions[r.Field] {
					item := map[string]any{"source_row": i + 1, "market_marker": row.MarketMarker, "source_index": f.Index, "unit": f.Unit, "period_basis": f.PeriodBasis, "definition_status": f.DefinitionStatus, "mapping_status": f.MappingStatus, "status": "missing_source_position"}
					if f.Index <= len(row.Fields) {
						v := row.Fields[f.Index-1]
						item["bits"] = v.Bits
						item["status"] = "nonfinite_source_value"
						if !math.IsNaN(v.Value) && !math.IsInf(v.Value, 0) {
							item["value"] = v.Value
							item["status"] = "source_nonzero_not_standard_approval"
							if v.Value == 0 {
								item["status"] = "source_zero_not_disclosure_approval"
							}
						}
					}
					matches = append(matches, item)
				}
			}
			if len(matches) > 0 {
				status = "source_matches_require_standard_chain_check"
			}
			results = append(results, map[string]any{"code": r.Code, "period": r.Period, "field": r.Field, "package": name, "status": status, "source_evidence": matches})
		}
	}
	return map[string]any{"contract": "alphalake-cache-gap-audit-v1", "catalog_version": financial.CatalogVersion, "manifest_sha256": fmt.Sprintf("%x", sha256.Sum256(manifest)), "packages": packages, "results": results, "boundary": "仅已校验本地版本；代码命中不证明证券身份，源数值不替代标准审核，未检查上游当前版本。"}, nil
}

// 先限定请求的代码、期间和标准列，再展开少量宽行；不逐公司查询。
func standardChain(db *sql.DB, requests []request) ([]map[string]any, error) {
	ctx := context.Background()
	catalog, err := store.LoadSnapshotFields(ctx, db)
	if err != nil {
		return nil, err
	}
	known := map[string]bool{}
	for _, f := range catalog {
		known[f.Name] = true
	}
	wanted, names := []request{}, map[string]bool{}
	out := []map[string]any{}
	for _, r := range requests {
		if !known[r.Field] {
			out = append(out, map[string]any{"code": r.Code, "period": r.Period, "field": r.Field, "status": "standard_field_not_installed"})
			continue
		}
		wanted = append(wanted, r)
		names[r.Field] = true
	}
	if len(wanted) == 0 {
		return out, nil
	}
	fields := []string{}
	for name := range names {
		fields = append(fields, `"`+name+`"`)
	}
	sort.Strings(fields)
	values := []string{}
	for _, f := range fields {
		values = append(values, "CAST(w."+f+" AS VARCHAR) AS "+f)
	}
	raw, err := json.Marshal(wanted)
	if err != nil {
		return nil, err
	}
	query := `WITH requests AS (
 SELECT value->>'code' code,(value->>'period')::DATE period,value->>'field' field FROM json_each(?)
 ), headers AS MATERIALIZED (
 SELECT r.*,a.sha256 AS artifact_sha256 FROM fundamental.source_record r JOIN meta.artifact a USING(artifact_id)
 WHERE EXISTS(SELECT 1 FROM requests q WHERE q.code=r.provider_code AND q.period=r.report_period)
 QUALIFY row_number() OVER(PARTITION BY r.provider_code,r.instrument_id,r.report_period ORDER BY a.fetched_at DESC,a.artifact_id DESC,r.source_record_id DESC)=1
 ), selected AS (
 SELECT h.source_record_id,h.provider_code AS code,h.report_period AS period,h.instrument_id,h.artifact_sha256,` + strings.Join(values, ",") + `
 FROM headers h LEFT JOIN fundamental.statement_snapshot w USING(source_record_id)
 ), cells AS (SELECT * FROM selected UNPIVOT INCLUDE NULLS (value FOR field IN (` + strings.Join(fields, ",") + `))), evidence AS (
 SELECT q.code,CAST(q.period AS VARCHAR) AS period,q.field,c.source_record_id,c.instrument_id,c.artifact_sha256,c.value,
 (SELECT coalesce(list(j.rule_code ORDER BY j.rule_code),[]) FROM fundamental.statement_rejection j
 WHERE j.source_record_id=c.source_record_id AND list_contains(j.fields,q.field)) AS rejection_rules
 FROM requests q LEFT JOIN cells c ON c.code=q.code AND c.period=q.period AND c.field=q.field
 ) SELECT CAST(to_json(t) AS VARCHAR) FROM (
 SELECT *,CASE WHEN source_record_id IS NULL THEN 'no_source_record'
 WHEN instrument_id IS NULL THEN 'unresolved_source_identity'
 WHEN value IS NOT NULL THEN 'available_standard_fact'
 WHEN list_contains(rejection_rules,'provider_zero_ambiguous') THEN 'rejected_source_zero'
 WHEN len(rejection_rules)>0 THEN 'rejected_standard_fact'
 ELSE 'missing_standard_fact_without_rejection' END AS status
 FROM evidence ORDER BY code,period,field,instrument_id,source_record_id) t`
	rows, err := db.QueryContext(ctx, query, string(raw))
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var raw string
		if err = rows.Scan(&raw); err != nil {
			return nil, err
		}
		var r map[string]any
		if err = json.Unmarshal([]byte(raw), &r); err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

func main() {
	cache := flag.String("cache", "", "flat authoritative TDX cache")
	input := flag.String("requests", "", "JSON array: code, period, standard field")
	database := flag.String("database", "", "optional authoritative DuckDB: verify historical cache receipts explicitly")
	flag.Parse()
	raw, err := os.ReadFile(*input)
	var requests []request
	if err == nil {
		decoder := json.NewDecoder(strings.NewReader(string(raw)))
		decoder.DisallowUnknownFields()
		err = decoder.Decode(&requests)
	}
	if err == nil && len(requests) == 0 {
		err = fmt.Errorf("nonempty requests required")
	}
	var result map[string]any
	var db *sql.DB
	if err == nil && *database != "" {
		db, err = store.OpenReadOnly(context.Background(), *database)
		if err == nil {
			defer db.Close()
			db.SetMaxOpenConns(1)
		}
	}
	if err == nil {
		result, err = audit(*cache, requests, db)
	}
	if err == nil && db != nil {
		result["standard_chain"], err = standardChain(db, requests)
	}
	if err == nil {
		err = json.NewEncoder(os.Stdout).Encode(result)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
