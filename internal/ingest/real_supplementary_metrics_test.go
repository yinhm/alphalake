package ingest

import (
	"math"
	"path/filepath"
	"testing"

	duck "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestRealSupplementaryMetrics(t *testing.T) {
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
	_, e = db.ExecContext(ctx, `CREATE TEMP TABLE previous_facts AS SELECT * FROM fundamental.financial_observations(NULL,NULL,NULL,NULL)`)
	check(e)
	restoreCurrentMappings(t, db, "notes LIKE 'supplementary-metrics-20260925;%'")
	rows, e := db.QueryContext(ctx, `SELECT provider_field FROM fundamental.provider_field WHERE notes LIKE 'supplementary-metrics-20260925;%' ORDER BY provider_field`)
	check(e)
	var fields []string
	for rows.Next() {
		var f string
		check(rows.Scan(&f))
		fields = append(fields, f)
	}
	check(rows.Err())
	check(rows.Close())
	if len(fields) != 65 {
		t.Fatal("official batch denominator", len(fields))
	}
	result, e := MaterializeProviderFundamentals(ctx, db, "tdx", fields...)
	check(e)
	if result.Inserted == 0 || result.Updated != 0 || result.Removed != 0 {
		t.Fatal(result)
	}
	var changed int
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM (SELECT * FROM previous_facts EXCEPT SELECT * FROM fundamental.financial_observations(NULL,NULL,NULL,NULL))`).Scan(&changed))
	if changed != 0 {
		t.Fatal("old facts changed")
	}
	// Every new real source cell must either materialize with its declared scale
	// and report basis or remain an explicit zero/nonfinite rejection.
	rows, e = sourceEvidenceDB(t, ctx, db).QueryContext(ctx, `SELECT p.value_float32_bits,m.value_multiplier,m.unit,m.period_basis,m.value_kind,CAST(p.report_period AS VARCHAR),f.value,f.unit,f.period_type,f.currency
 FROM _source_evidence p JOIN fundamental.provider_field m ON p.source=m.source AND p.provider_field=m.provider_field
 LEFT JOIN fundamental.financial_observations(NULL,NULL,NULL,NULL) f ON f.fact_id=p.provider_fact_id
 WHERE m.notes LIKE 'supplementary-metrics-20260925;%'`)
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
		if value == nil || math.Abs(*value-raw*mult) > 1e-9 || gotUnit == nil || *gotUnit != unit {
			t.Fatal("unit/value differs", raw, value, mult, unit)
		}
		month := period[5:7]
		expected := map[string]string{"03": "Q1", "06": "H1", "09": "9M", "12": "FY"}[month]
		if basis == "instant" || basis == "opening_instant" {
			expected = basis
		}
		if basis == "ttm" {
			expected = "TTM"
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
	var n int
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.ttm_asof('2026-09-25',DATE '2026-06-30') WHERE canonical_field IN (SELECT name FROM fundamental.source_field WHERE period_basis='ttm' OR value_kind IN ('per_share','count'))`).Scan(&n))
	if n != 0 {
		t.Fatal("nonadditive source metrics acquired derived TTM", n)
	}
	_, e = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET value_multiplier=-1 WHERE notes LIKE 'supplementary-metrics-20260925;%'`)
	check(e)
	rejected, e := MaterializeProviderFundamentals(ctx, db, "tdx", fields...)
	check(e)
	if rejected.Removed != result.Inserted {
		t.Fatal("invalid mapping survived", rejected)
	}
	_, e = db.ExecContext(ctx, `UPDATE fundamental.provider_field m SET value_multiplier=s.value_multiplier FROM fundamental.source_field s WHERE s.source=m.source AND s.provider_field=m.provider_field AND m.notes LIKE 'supplementary-metrics-20260925;%'`)
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
	t.Logf("new facts %d; ambiguous zeros %d; official positions %d", checked, zero, len(fields))
}
