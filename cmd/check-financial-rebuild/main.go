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
	if rebuilt != checked {
		panic(fmt.Sprintf("standard denominator changed: old=%d new=%d", checked, rebuilt))
	}
	comparisonSeconds := time.Since(start).Seconds()
	_, e = db.ExecContext(ctx, `DETACH previous`)
	must(e)
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
	must(json.NewEncoder(os.Stdout).Encode(map[string]any{"standard_values_compared": checked, "mismatches": 0, "revision_periods": len(parts), "instruments": instruments, "first_period": firstPeriod, "last_period": lastPeriod, "seconds": comparisonSeconds, "history_query": map[string]any{"code": *queryCode, "asof": asof, "milliseconds": timings, "json_bytes": queryBytes, "prepare_milliseconds": preparationMillis, "cache": "one prepared statement, same process; OS page cache not cleared", "projection": "all standard fields and financial semantics; source evidence excluded"}, "scope": "exact decimals, units, currencies, periods, scope, announcements, filing IDs, source field and revision; row IDs deliberately change"}))
}
