// audit-financial-field scans catalogued fields in one pass in the main database's archived
// SH/SZ source records. Source versions remain separate; it never changes policy.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"math"
	"os"
	"strings"

	f "github.com/yinhm/alphalake/internal/source/tdx/financial"
	store "github.com/yinhm/alphalake/internal/store/duckdb"
)

type counts struct {
	Records int `json:"records"`
	Zero    int `json:"zero"`
	Nonzero int `json:"nonzero"`
	Missing int `json:"missing_position"`
	Invalid int `json:"invalid_value"`
}

func (c *counts) add(values int, index int, value float64) string {
	c.Records++
	switch {
	case index >= values:
		c.Missing++
		return "missing_position"
	case math.IsNaN(value) || math.IsInf(value, 0):
		c.Invalid++
		return "invalid_value"
	case value == 0:
		c.Zero++
		return "zero"
	default:
		c.Nonzero++
		return "nonzero"
	}
}
func run() error {
	dbpath := flag.String("database", "workspace/alphalake.duckdb", "authoritative database, read only")
	name := flag.String("field", "", "comma-separated standard field names in the source catalog")
	flag.Parse()
	catalog, err := f.FieldCatalog()
	if err != nil {
		return err
	}
	fields, err := selectFields(catalog, *name)
	if err != nil {
		return err
	}
	ctx := context.Background()
	db, err := store.OpenReadOnly(ctx, *dbpath)
	if err != nil {
		return err
	}
	defer db.Close()
	root, err := store.FinancialArchiveRoot(ctx, db)
	if err != nil {
		return err
	}
	rows, err := db.QueryContext(ctx, `SELECT DISTINCT a.artifact_id,a.local_path,a.source_locator,a.sha256,a.content_length
 FROM fundamental.source_record r JOIN meta.artifact a USING(artifact_id) JOIN core.instrument i USING(instrument_id)
 WHERE i.exchange_mic IN ('XSHG','XSHE') AND a.source='tdx' AND a.dataset='professional_financial' ORDER BY a.artifact_id`)
	if err != nil {
		return err
	}
	type artifact struct {
		id, size         int64
		path, name, hash string
	}
	var artifacts []artifact
	for rows.Next() {
		var a artifact
		if err = rows.Scan(&a.id, &a.path, &a.name, &a.hash, &a.size); err != nil {
			rows.Close()
			return err
		}
		artifacts = append(artifacts, a)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	totals := make([]counts, len(fields))
	versions := []map[string]any{}
	securities := map[int64]bool{}
	for _, a := range artifacts {
		records, err := db.QueryContext(ctx, `SELECT r.source_row,r.provider_code,CAST(r.report_period AS VARCHAR),r.instrument_id FROM fundamental.source_record r JOIN core.instrument i USING(instrument_id) WHERE r.artifact_id=? AND i.exchange_mic IN ('XSHG','XSHE') ORDER BY r.source_row`, a.id)
		if err != nil {
			return err
		}
		type locator struct {
			row          int
			code, period string
			id           int64
		}
		var locations []locator
		for records.Next() {
			var r locator
			if err = records.Scan(&r.row, &r.code, &r.period, &r.id); err != nil {
				records.Close()
				return err
			}
			locations = append(locations, r)
		}
		err = records.Err()
		records.Close()
		if err != nil {
			return err
		}
		pkg, err := store.ReadFinancialArchive(root, a.path, a.name, a.hash, a.size)
		if err != nil {
			return err
		}
		versionFields := []map[string]any{}
		for i, field := range fields {
			c := counts{}
			samples := map[string][]map[string]any{}
			for _, r := range locations {
				if r.row < 1 || r.row > len(pkg.Records) {
					return fmt.Errorf("source row out of range")
				}
				record := pkg.Records[r.row-1]
				if record.Code != r.code || record.ReportPeriod.Format("2006-01-02") != r.period {
					return fmt.Errorf("source identity or period mismatch")
				}
				index := field.Index - 1
				value := 0.0
				if index < len(record.Fields) {
					value = record.Fields[index].Value * *field.Multiplier
				}
				state := c.add(len(record.Fields), index, value)
				totals[i].add(len(record.Fields), index, value)
				securities[r.id] = true
				if len(samples[state]) < 3 {
					s := map[string]any{"code": r.code, "period": r.period, "source_row": r.row}
					if state == "zero" || state == "nonzero" {
						s["value"] = value
					}
					samples[state] = append(samples[state], s)
				}
			}
			versionFields = append(versionFields, map[string]any{"field": field.Name, "source_evidence": map[string]any{"source_position": field.Index}, "counts": c, "samples": samples})
		}
		versions = append(versions, map[string]any{"artifact_sha256": a.hash, "archive": a.path, "fields": versionFields})
	}
	summary := []map[string]any{}
	for i, field := range fields {
		summary = append(summary, map[string]any{"field": field.Name, "unit": field.Unit, "period_basis": field.PeriodBasis, "source_evidence": map[string]any{"source_position": field.Index}, "counts": totals[i]})
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{"fields": summary, "scope": "all_retained_source_versions_for_resolved_SH_SZ_identities; records_are_not_unique_company_periods_or_PIT_facts", "securities": len(securities), "versions": versions, "automatic_policy_approval": false})

}
func selectFields(catalog []f.FieldDefinition, names string) ([]f.FieldDefinition, error) {
	selected := []f.FieldDefinition{}
	seen := map[string]bool{}
	for _, name := range strings.Split(names, ",") {
		name = strings.TrimSpace(name)
		if name == "" || seen[name] {
			return nil, fmt.Errorf("nonempty distinct standard fields required")
		}
		seen[name] = true
		found := false
		for _, field := range catalog {
			if field.Name == name && field.DefinitionStatus == "official" && field.Multiplier != nil && (*field.Multiplier == 1 || *field.Multiplier == 10000) {
				selected = append(selected, field)
				found = true
			}
		}
		if !found {
			return nil, fmt.Errorf("official named field with a defined multiplier required: %s", name)
		}
	}
	return selected, nil
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
