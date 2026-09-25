// check-financial-rebuild compares every retained standard value and its
// financial semantics across the explicit schema51-to-wide rebuild.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	store "github.com/yinhm/alphalake/internal/store/duckdb"
)

func must(e error) {
	if e != nil {
		panic(e)
	}
}
func main() {
	old := flag.String("previous", "", "original schema51 database")
	candidate := flag.String("candidate", "", "wide candidate database")
	allowAdditions := flag.Bool("allow-additions", false, "after explicit sync: compare retained standard revisions only; permit new facts and operational metadata changes")
	backup := flag.String("older-backup", "", "optional obsolete backup to archive unique historical rows before removal")
	audit := flag.String("audit", "", "rebuild audit directory; required with older-backup")
	queryCode := flag.String("query-code", "300866", "security for complete-history query timing")
	profile := flag.String("query-profile", "", "new DuckDB JSON query profile path")
	flag.Parse()
	if *old == "" || *candidate == "" {
		flag.Usage()
		os.Exit(2)
	}
	ctx := context.Background()
	db, e := store.OpenReadOnly(ctx, *candidate)
	must(e)
	defer db.Close()
	db.SetMaxOpenConns(1)
	path, e := filepath.Abs(*old)
	must(e)
	_, e = db.ExecContext(ctx, `ATTACH '`+strings.ReplaceAll(path, "'", "''")+`' AS previous (READ_ONLY)`)
	must(e)
	rows, e := db.QueryContext(ctx, `SELECT revision_key,report_period,count(*) FROM previous.fundamental.fact GROUP BY revision_key,report_period ORDER BY report_period,revision_key`)
	must(e)
	type part struct {
		revision string
		period   time.Time
		count    int64
	}
	parts := []part{}
	for rows.Next() {
		var p part
		must(rows.Scan(&p.revision, &p.period, &p.count))
		parts = append(parts, p)
	}
	must(rows.Err())
	must(rows.Close())
	var checked int64
	start := time.Now()
	for _, p := range parts {
		var mismatches int64
		must(db.QueryRowContext(ctx, `WITH rebuilt AS (SELECT * FROM fundamental.financial_observations(NULL,?,?,NULL) WHERE revision_key=?)
 SELECT count(*) FROM previous.fundamental.fact old LEFT JOIN rebuilt new
 ON new.instrument_id=old.instrument_id AND new.provider_code=old.provider_code AND new.report_period=old.report_period AND new.canonical_field=old.canonical_field AND new.revision_key=old.revision_key
 WHERE old.revision_key=? AND old.report_period=? AND (new.fact_id IS NULL OR old.value IS DISTINCT FROM new.value OR old.unit IS DISTINCT FROM new.unit OR old.currency IS DISTINCT FROM new.currency OR old.period_type IS DISTINCT FROM new.period_type OR old.statement_scope IS DISTINCT FROM new.statement_scope OR old.announcement_time IS DISTINCT FROM new.announcement_time OR old.source_filing_id IS DISTINCT FROM new.source_filing_id OR old.source_provider_field IS DISTINCT FROM new.source_provider_field)`, p.period, p.period, p.revision, p.revision, p.period).Scan(&mismatches))
		if mismatches != 0 {
			panic(fmt.Sprintf("%s %s: %d mismatched/missing standard cells", p.period.Format("2006-01-02"), p.revision, mismatches))
		}
		checked += p.count
		fmt.Fprintf(os.Stderr, "checked %s: %d cells, cumulative=%d\n", p.period.Format("2006-01-02"), p.count, checked)
	}
	var rebuilt int64
	must(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.financial_observations(NULL,NULL,NULL,NULL)`).Scan(&rebuilt))
	if rebuilt < checked || (!*allowAdditions && rebuilt != checked) {
		panic(fmt.Sprintf("standard denominator changed: old=%d new=%d", checked, rebuilt))
	}

	// Compare actual version selection as well as retained revisions. Equal
	// announcement times must not silently select different financial amounts.
	periods := map[string]time.Time{}
	for _, p := range parts {
		periods[p.period.Format("2006-01-02")] = p.period
	}
	var latestCells int64
	var tables []string
	if !*allowAdditions {
		for _, period := range periods {
			var differences, n int64
			must(db.QueryRowContext(ctx, `WITH old AS (SELECT * FROM previous.fundamental.fact WHERE report_period=? QUALIFY row_number() OVER(PARTITION BY instrument_id,canonical_field,report_period ORDER BY announcement_time DESC,fact_id DESC)=1), new AS (SELECT * FROM fundamental.financial_observations_asof(NULL,?,?,NULL))
 SELECT count(*),count(*) FILTER(WHERE new.fact_id IS NULL OR old.value IS DISTINCT FROM new.value OR old.unit IS DISTINCT FROM new.unit OR old.period_type IS DISTINCT FROM new.period_type OR old.source_filing_id IS DISTINCT FROM new.source_filing_id OR old.provider_code IS DISTINCT FROM new.provider_code)
 FROM old LEFT JOIN new USING(instrument_id,canonical_field,report_period)`, period, period, period).Scan(&n, &differences))
			if differences != 0 {
				panic(fmt.Sprintf("latest selection %s differs in %d cells", period.Format("2006-01-02"), differences))
			}
			latestCells += n
		}
		rows, e = db.QueryContext(ctx, `SELECT table_schema,table_name FROM information_schema.tables WHERE table_catalog='previous' AND table_type='BASE TABLE' AND NOT (table_schema='fundamental' AND table_name IN('fact','provider_fact')) AND NOT(table_schema='meta' AND table_name IN('validation_result','schema_version')) ORDER BY table_schema,table_name`)
		must(e)
		for rows.Next() {
			var schema, table string
			must(rows.Scan(&schema, &table))
			tables = append(tables, `"`+strings.ReplaceAll(schema, `"`, `""`)+`"."`+strings.ReplaceAll(table, `"`, `""`)+`"`)
		}
		must(rows.Err())
		must(rows.Close())
		for _, table := range tables {
			var missing int64
			must(db.QueryRowContext(ctx, `SELECT count(*) FROM (SELECT * FROM previous.`+table+` EXCEPT SELECT * FROM `+table+`)`).Scan(&missing))
			if missing != 0 {
				panic(fmt.Sprintf("%s lost/changed %d governance or other-domain records", table, missing))
			}
		}
	}
	comparisonSeconds := time.Since(start).Seconds()
	_, e = db.ExecContext(ctx, `DETACH previous`)
	must(e)
	if *backup != "" {
		if *audit == "" {
			panic("audit directory required with older-backup")
		}
		archiveDir := filepath.Join(*audit, "older-backup-unique")
		must(os.Mkdir(archiveDir, 0700))
		backupPath, e := filepath.Abs(*backup)
		must(e)
		_, e = db.ExecContext(ctx, `ATTACH '`+strings.ReplaceAll(backupPath, "'", "''")+`' AS older (READ_ONLY)`)
		must(e)
		rows, e = db.QueryContext(ctx, `SELECT table_schema,table_name FROM information_schema.tables WHERE table_catalog='older' AND table_type='BASE TABLE' AND NOT(table_schema='fundamental' AND table_name='provider_fact') AND NOT(table_schema='meta' AND table_name='schema_version') ORDER BY table_schema,table_name`)
		must(e)
		type tableName struct{ schema, name string }
		var olderTables []tableName
		for rows.Next() {
			var t tableName
			must(rows.Scan(&t.schema, &t.name))
			olderTables = append(olderTables, t)
		}
		must(rows.Err())
		must(rows.Close())
		for _, t := range olderTables {
			table := `"` + strings.ReplaceAll(t.schema, `"`, `""`) + `"."` + strings.ReplaceAll(t.name, `"`, `""`) + `"`
			reference := table
			if t.schema == "fundamental" && t.name == "fact" {
				reference = `read_parquet('` + strings.ReplaceAll(filepath.Join(*audit, "standard-history.parquet"), "'", "''") + `')`
			}
			if t.schema == "meta" && t.name == "validation_result" {
				reference = `read_parquet('` + strings.ReplaceAll(filepath.Join(*audit, "validation-history.parquet"), "'", "''") + `')`
			}
			var maxID int64
			if t.schema == "meta" && t.name == "validation_result" {
				must(db.QueryRowContext(ctx, `SELECT coalesce(max(validation_result_id),0) FROM older.meta.validation_result`).Scan(&maxID))
			}
			var total int64
			for lower := int64(0); ; lower += 250000 {
				predicate, suffix := "", ""
				if maxID > 0 {
					predicate = fmt.Sprintf(" WHERE validation_result_id>%d AND validation_result_id<=%d", lower, lower+250000)
					suffix = fmt.Sprintf("-%d", lower)
				}
				destination := filepath.Join(archiveDir, t.schema+"."+t.name+suffix+".parquet")
				_, e = db.ExecContext(ctx, `COPY (SELECT * FROM older.`+table+predicate+` EXCEPT SELECT * FROM `+reference+predicate+`) TO '`+strings.ReplaceAll(destination, "'", "''")+`' (FORMAT PARQUET,COMPRESSION ZSTD)`)
				must(e)
				var n int64
				must(db.QueryRowContext(ctx, `SELECT count(*) FROM read_parquet('`+strings.ReplaceAll(destination, "'", "''")+`')`).Scan(&n))
				total += n
				if n == 0 {
					must(os.Remove(destination))
				}
				if maxID == 0 || lower+250000 >= maxID {
					break
				}
			}
			fmt.Fprintf(os.Stderr, "older backup unique %s: %d rows\n", table, total)
		}
		_, e = db.ExecContext(ctx, `DETACH older`)
		must(e)
	}
	var asof, firstPeriod, lastPeriod time.Time
	var instruments int64
	must(db.QueryRowContext(ctx, `SELECT max(announcement_time),min(report_period),max(report_period),count(DISTINCT instrument_id) FROM fundamental.statement_snapshot`).Scan(&asof, &firstPeriod, &lastPeriod, &instruments))
	if *profile != "" {
		if _, err := os.Stat(*profile); !os.IsNotExist(err) {
			panic("query profile path must not exist")
		}
		_, e = db.ExecContext(ctx, `SET enable_profiling='json'; SET profiling_mode='detailed'; SET profiling_output='`+strings.ReplaceAll(*profile, "'", "''")+`'`)
		must(e)
	}
	preparationStart := time.Now()
	statement, e := db.PrepareContext(ctx, `SELECT coalesce(CAST(to_json(list(s)) AS VARCHAR),'[]') FROM (SELECT instrument_id,provider_code AS code,report_period,canonical_field AS field,CAST(value AS VARCHAR) AS value,unit,currency,period_type,statement_scope,announcement_time,fact_id FROM fundamental.financial_observations_asof(?,NULL,NULL,?)) s`)
	must(e)
	defer statement.Close()
	preparationMillis := float64(time.Since(preparationStart).Microseconds()) / 1000
	timings := make([]float64, 3)
	queryBytes := 0
	for i := range timings {
		started := time.Now()
		var output string
		must(statement.QueryRowContext(ctx, *queryCode, asof).Scan(&output))
		timings[i] = float64(time.Since(started).Microseconds()) / 1000
		queryBytes = len(output)
		if queryBytes <= 2 {
			panic("history benchmark returned no rows")
		}
	}
	if *profile != "" {
		_, e = db.ExecContext(ctx, `PRAGMA disable_profiling`)
		must(e)
	}
	must(json.NewEncoder(os.Stdout).Encode(map[string]any{"standard_values_compared": checked, "candidate_standard_values": rebuilt, "allow_additions": *allowAdditions, "latest_cells_compared": latestCells, "governance_tables_compared": len(tables), "mismatches": 0, "revision_periods": len(parts), "instruments": instruments, "first_period": firstPeriod, "last_period": lastPeriod, "seconds": comparisonSeconds, "history_query": map[string]any{"code": *queryCode, "asof": asof, "milliseconds": timings, "json_bytes": queryBytes, "prepare_milliseconds": preparationMillis, "cache": "one prepared statement, same process; OS page cache not cleared", "projection": "all standard fields and financial semantics; source evidence excluded"}, "scope": "exact decimals, units, currencies, periods, scope, announcements, filing IDs, source field and revision; row IDs deliberately change"}))
}
