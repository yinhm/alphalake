package duckdb

import (
	"database/sql"
	"fmt"
	"path/filepath"
	"testing"
)

func TestFinancialWindows(t *testing.T) {
	ctx := t.Context()
	db, err := OpenInitialized(ctx, filepath.Join(t.TempDir(), "windows.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	check := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	// 合成标准事实仅用于组合规则；真实源链路在 ingest 的六季度样本中验收。
	seed := func(field, name, period, kind, announced string, value int) int64 {
		t.Helper()
		var id int64
		check(db.QueryRowContext(ctx, `INSERT INTO fundamental.fact
			(instrument_id, canonical_field, report_period, announcement_time, period_type,
			statement_scope, currency, unit, value, primary_source, source_provider_field,
			provider_code, source_filing_id, revision_key, normalization_rule, materializer_version)
			VALUES (1, ?, CAST(? AS DATE), CAST(? AS TIMESTAMPTZ), ?, 'provider_default', 'CNY', 'CNY', ?,
			'tdx', ?, '002920', 1, ?, 'test', 'test') RETURNING fact_id`, name, period, announced, kind, value,
			field, field+period+announced).Scan(&id))
		return id
	}
	for i, period := range []string{"2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"} {
		seed("FN230", "revenue", period, fmt.Sprintf("Q%d", i+1), "2026-03-01T00:00:00Z", 10*(i+1))
	}
	seed("FN230", "revenue", "2026-03-31", "Q1", "2026-04-30T16:00:00Z", 50)
	seed("FN93", "income_tax_expense", "2025-03-31", "Q1", "2026-03-01T00:00:00Z", 1)
	seed("FN93", "income_tax_expense", "2025-12-31", "FY", "2026-03-01T00:00:00Z", 9)
	seed("FN93", "income_tax_expense", "2026-03-31", "Q1", "2026-04-30T16:00:00Z", 3)
	seed("FN8", "monetary_funds", "2025-12-31", "instant", "2026-03-01T00:00:00Z", 450)
	seed("FN8", "monetary_funds", "2026-03-31", "instant", "2026-04-30T16:00:00Z", 500)
	assert := func(asOf, end, field string, want sql.NullFloat64, available, required int) {
		t.Helper()
		var value sql.NullFloat64
		var gotAvailable, gotRequired, missing int
		var status string
		for _, scope := range []string{"", ", min_instrument_id := 1, max_instrument_id := 1"} {
			check(db.QueryRowContext(ctx, `SELECT CAST(value AS DOUBLE), available_inputs, required_inputs,
			coverage_status, coalesce(len(missing_periods),0) FROM fundamental.ttm_asof(?, CAST(? AS DATE)`+scope+`)
			WHERE instrument_id=1 AND canonical_field=?`, asOf, end, field).Scan(&value, &gotAvailable, &gotRequired, &status, &missing))
			if value != want || gotAvailable != available || gotRequired != required || missing != required-available || (status == "complete") != want.Valid {
				t.Fatalf("%s/%s: value=%v inputs=%d/%d missing=%d status=%s", end, field, value, gotAvailable, gotRequired, missing, status)
			}
		}
	}
	before, after := "2026-04-30T15:59:59Z", "2026-04-30T16:00:00Z"
	assert(before, "2026-03-31", "revenue", sql.NullFloat64{}, 3, 4)
	assert(before, "2026-03-31", "monetary_funds", sql.NullFloat64{}, 0, 1)
	assert(after, "2026-03-31", "revenue", sql.NullFloat64{Float64: 140, Valid: true}, 4, 4)
	assert(after, "2026-03-31", "income_tax_expense", sql.NullFloat64{Float64: 11, Valid: true}, 3, 3)
	assert(after, "2026-03-31", "monetary_funds", sql.NullFloat64{Float64: 500, Valid: true}, 1, 1)
	assert(after, "2026-03-31", "bonds_payable", sql.NullFloat64{}, 0, 1)
	for field, want := range map[string]int{"revenue": 100, "income_tax_expense": 9, "monetary_funds": 450} {
		var value int
		var kind string
		check(db.QueryRowContext(ctx, `SELECT value, period_type FROM fundamental.annual_asof(?, 2025)
			WHERE instrument_id=1 AND canonical_field=?`, after, field).Scan(&value, &kind))
		if value != want || (kind == "instant") != (field == "monetary_funds") {
			t.Fatalf("annual %s=%d/%s", field, value, kind)
		}
	}
	corrected := seed("FN230", "revenue", "2025-09-30", "Q3", "2026-05-10T16:00:00Z", 35)
	assert("2026-05-10T15:59:59Z", "2026-03-31", "revenue", sql.NullFloat64{Float64: 140, Valid: true}, 4, 4)
	assert("2026-05-10T16:00:00Z", "2026-03-31", "revenue", sql.NullFloat64{Float64: 145, Valid: true}, 4, 4)
	var linked bool
	check(db.QueryRowContext(ctx, `SELECT list_contains(source_fact_ids, ?) FROM fundamental.ttm_asof('2026-05-11', DATE '2026-03-31')
		WHERE canonical_field='revenue'`, corrected).Scan(&linked))
	if !linked {
		t.Fatal("missing corrected source lineage")
	}
	_, err = db.ExecContext(ctx, `DELETE FROM fundamental.fact WHERE report_period=DATE '2025-06-30'`)
	check(err)
	assert("2026-05-11", "2026-03-31", "revenue", sql.NullFloat64{}, 3, 4)
	_, err = db.ExecContext(ctx, `UPDATE fundamental.fact SET unit='USD' WHERE canonical_field='income_tax_expense' AND report_period=DATE '2025-12-31'`)
	check(err)
	assert(after, "2026-03-31", "income_tax_expense", sql.NullFloat64{}, 2, 3)
	var invalidEnd int
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.ttm_asof(?, DATE '2026-03-30')`, after).Scan(&invalidEnd))
	if invalidEnd != 0 {
		t.Fatal("non-quarter-end accepted")
	}
	for _, bounds := range [][2]int64{{2, 2}, {1, 0}} {
		var count int
		check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.ttm_asof(?, DATE '2026-03-31', min_instrument_id := ?, max_instrument_id := ?)`, after, bounds[0], bounds[1]).Scan(&count))
		if count != 0 {
			t.Fatalf("identity outside scope %v: %d rows", bounds, count)
		}
	}
	// 标准派生不读源目录、不以供应商或字段编号决定语义。
	_, err = db.ExecContext(ctx, `DELETE FROM fundamental.provider_field;
        UPDATE fundamental.fact SET primary_source='another_vendor', source_provider_field='opaque_source_key'`)
	check(err)
	assert(after, "2026-03-31", "monetary_funds", sql.NullFloat64{Float64: 500, Valid: true}, 1, 1)
	assert(after, "2026-03-31", "income_tax_expense", sql.NullFloat64{}, 2, 3)

}
