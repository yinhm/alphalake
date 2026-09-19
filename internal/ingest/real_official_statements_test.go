package ingest

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	duck "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestRealOfficialStatements(t *testing.T) {
	ctx := t.Context()
	out := t.TempDir()
	t.Setenv("ALPHALAKE_VALUATION_EXPORT_DIR", out)
	if !t.Run("frozen_baseline", TestRealValuationStandardChain) {
		t.Fatal("baseline failed")
	}
	check := func(e error) {
		t.Helper()
		if e != nil {
			t.Fatal(e)
		}
	}
	dbPath := filepath.Join(out, "acceptance.duckdb")
	db, e := duck.OpenInitialized(ctx, dbPath)
	check(e)
	defer func() { db.Close() }()
	_, e = db.ExecContext(ctx, `CREATE TEMP TABLE previous_facts AS SELECT * FROM fundamental.fact`)
	check(e)
	restoreCurrentMappings(t, db, "notes LIKE 'official-statements-20260919;%'")
	rows, e := db.QueryContext(ctx, `SELECT provider_field FROM fundamental.provider_field WHERE notes LIKE 'official-statements-20260919;%' ORDER BY provider_field`)
	check(e)
	var fields []string
	for rows.Next() {
		var f string
		check(rows.Scan(&f))
		fields = append(fields, f)
	}
	check(rows.Err())
	check(rows.Close())
	if len(fields) != 124 {
		t.Fatal("official batch denominator", len(fields))
	}
	result, e := MaterializeProviderFundamentals(ctx, db, "tdx", fields...)
	check(e)
	if result.Inserted == 0 || result.Updated != 0 || result.Removed != 0 {
		t.Fatal(result)
	}
	var changed int
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM (SELECT * FROM previous_facts EXCEPT SELECT * FROM fundamental.fact)`).Scan(&changed))
	if changed != 0 {
		t.Fatal("old facts changed")
	}
	// Every new real source cell must either materialize with its declared scale
	// and report basis or remain an explicit zero/nonfinite rejection.
	rows, e = db.QueryContext(ctx, `SELECT p.value_float32_bits,m.value_multiplier,m.unit,m.period_basis,m.value_kind,CAST(p.report_period AS VARCHAR),f.value,f.unit,f.period_type,f.currency
 FROM fundamental.provider_fact p JOIN fundamental.provider_field m ON p.source=m.source AND p.provider_field=m.provider_field
 LEFT JOIN fundamental.fact f ON f.provider_fact_id=p.provider_fact_id
 WHERE m.notes LIKE 'official-statements-20260919;%'`)
	check(e)
	checked, zero := 0, 0
	for rows.Next() {
		var bits uint32
		var mult float64
		var unit, basis, kind, period string
		var value *float64
		var gotUnit, gotBasis, currency *string
		check(rows.Scan(&bits, &mult, &unit, &basis, &kind, &period, &value, &gotUnit, &gotBasis, &currency))
		raw := float64(math.Float32frombits(bits))
		if raw == 0 {
			if value != nil {
				t.Fatal("zero promoted")
			}
			zero++
			continue
		}
		if value == nil || math.Abs(*value-raw*mult) > 1e-9 || gotUnit == nil || *gotUnit != unit || currency == nil || *currency != "CNY" {
			t.Fatal("unit/value differs", raw, value, mult, unit)
		}
		month := period[5:7]
		expected := map[string]string{"03": "Q1", "06": "H1", "09": "9M", "12": "FY"}[month]
		if basis == "instant" || basis == "opening_instant" {
			expected = basis
		}
		if basis == "quarter" {
			expected = map[string]string{"03": "Q1", "06": "Q2", "09": "Q3", "12": "Q4"}[month]
		}
		if gotBasis == nil || *gotBasis != expected {
			t.Fatal("period differs", period, basis, gotBasis)
		}
		checked++
	}
	check(rows.Err())
	check(rows.Close())
	if checked != result.Inserted {
		t.Fatal(checked, result)
	}
	end := time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC)
	at := time.Date(2026, 9, 19, 0, 0, 0, 0, time.UTC)
	for _, code := range []string{"300866", "600519"} {
		data, e := duck.ExportFinancialStatements(ctx, db, code, end, at, false)
		check(e)
		b, e := json.Marshal(data)
		check(e)
		if strings.Contains(string(b), "source_evidence") || strings.Contains(string(b), `"FN`) {
			t.Fatal("raw identifiers leaked")
		}
		statements := data["statements"].(map[string][]map[string]any)
		if len(statements) != 3 {
			t.Fatal("three statements required")
		}
		count := 0
		for _, items := range statements {
			count += len(items)
		}
		if count != 282 {
			t.Fatal("full statement denominator", count)
		}
		_, e = duck.ExportFinancialStatements(ctx, db, code, end, at, true)
		check(e)
		if dir := os.Getenv("ALPHALAKE_STATEMENT_ACCEPTANCE_DIR"); dir != "" {
			check(os.WriteFile(filepath.Join(dir, code+"-statements.json"), b, 0600))
		}
	}
	// Opening balances retain the report identity but expose the correct balance date.
	var balance, unit, basis, status, value string
	check(db.QueryRowContext(ctx, `SELECT CAST(balance_date AS VARCHAR),unit,period_basis,status,CAST(value AS VARCHAR) FROM fundamental.statements_asof(?,?,?) WHERE field='opening_cash_and_cash_equivalents'`, at, end, "300866").Scan(&balance, &unit, &basis, &status, &value))
	if balance != "2025-12-31" || unit != "CNY" || basis != "opening_instant" || status != "available" {
		t.Fatal(balance, unit, basis, status, value)
	}
	var n int
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.ttm_asof(?,?) WHERE canonical_field IN ('opening_cash_and_cash_equivalents','basic_earnings_per_share','diluted_earnings_per_share','basic_earnings_per_share_quarter')`, at, end).Scan(&n))
	if n != 0 {
		t.Fatal("nonadditive metrics acquired a false TTM")
	}
	for _, delta := range []time.Duration{-time.Nanosecond, 0} {
		cutoff := time.Date(2026, 8, 31, 16, 0, 0, 0, time.UTC).Add(delta)
		check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.statements_asof(?,?,?) WHERE field='basic_earnings_per_share' AND status='available'`, cutoff, end, "300866").Scan(&n))
		if (n == 1) != (delta == 0) {
			t.Fatal("snapshot announcement boundary", n)
		}
	}
	// Unknown securities remain in the full denominator, with no fabricated values.
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.statements_asof(?,?,?) WHERE status='unresolved_identity' AND value IS NULL`, at, end, "999999").Scan(&n))
	if n != 282 {
		t.Fatal("missing identity disappeared", n)
	}
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.statements_asof(?,?,?) WHERE status IN ('period_requires_review','ambiguous_source_variant') AND value IS NULL`, at, end, "300866").Scan(&n))
	if n != 2 {
		t.Fatal("unreviewed fields disappeared", n)
	}
	_, e = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET value_multiplier=-1 WHERE notes LIKE 'official-statements-20260919;%'`)
	check(e)
	rejected, e := MaterializeProviderFundamentals(ctx, db, "tdx", fields...)
	check(e)
	if rejected.Removed != result.Inserted {
		t.Fatal("invalid mapping survived", rejected)
	}
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.statements_asof(?,?,?) WHERE mapping_review='official_mapping' AND value IS NOT NULL`, at, end, "300866").Scan(&n))
	if n != 0 {
		t.Fatal("invalid snapshot amount survived")
	}
	_, e = db.ExecContext(ctx, `UPDATE fundamental.provider_field m SET value_multiplier=s.value_multiplier FROM fundamental.source_field s WHERE s.source=m.source AND s.provider_field=m.provider_field AND m.notes LIKE 'official-statements-20260919;%'`)
	check(e)
	restored, e := MaterializeProviderFundamentals(ctx, db, "tdx", fields...)
	check(e)
	if restored.Inserted != result.Inserted {
		t.Fatal("restore differs", restored)
	}
	check(db.Close())
	db, e = duck.OpenInitialized(ctx, dbPath)
	check(e)
	replay, e := MaterializeProviderFundamentals(ctx, db, "tdx", fields...)
	check(e)
	if replay.Inserted != 0 || replay.Updated != 0 || replay.Removed != 0 {
		t.Fatal(replay)
	}
	t.Logf("new facts %d; ambiguous zeros %d; official positions %d; three-statement rows 282", checked, zero, len(fields))
}
