package main

import (
	"context"
	"database/sql"
	"fmt"
)

// 仅发布045→046语义目录迁移；不允许借机物化或修改财务金额。
func checkStandardFields(ctx context.Context, db *sql.DB) (map[string]summary, error) {
	rows, err := db.QueryContext(ctx, `SELECT table_schema||'.'||table_name FROM information_schema.tables WHERE table_catalog='baseline' AND table_type='BASE TABLE' ORDER BY 1`)
	if err != nil {
		return nil, err
	}
	var tables []string
	for rows.Next() {
		var name string
		if err = rows.Scan(&name); err != nil {
			rows.Close()
			return nil, err
		}
		tables = append(tables, name)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return nil, err
	}
	summarize := func(q string) (summary, error) {
		var s summary
		e := db.QueryRowContext(ctx, `SELECT count(*),CAST(coalesce(bit_xor(hash(t)),0) AS VARCHAR),CAST(coalesce(sum(CAST(hash(t) AS HUGEINT)),0) AS VARCHAR) FROM (`+q+`) t`).Scan(&s.Rows, &s.XOR, &s.Sum)
		return s, e
	}
	checks := map[string]summary{}
	for _, name := range tables {
		before := "SELECT * FROM baseline." + name
		after := "SELECT * FROM candidate." + name
		switch name {
		case "fundamental.fact":
			before = `SELECT * REPLACE(CASE WHEN canonical_field='total_shares' AND materializer_version<>'legacy' THEN 'instant' ELSE period_type END AS period_type) FROM baseline.fundamental.fact`
		case "fundamental.provider_field":
			before = `SELECT * REPLACE(CASE WHEN source='tdx' AND provider_field IN ('FN230','FN231','FN232','FN233','FN234','FN235','FN236','FN237') THEN 'quarter' WHEN source='tdx' AND provider_field='FN238' THEN 'instant' ELSE period_basis END AS period_basis) FROM baseline.fundamental.provider_field`
		case "meta.schema_version":
			after += " WHERE version<=45"
		}
		a, e := summarize(before)
		if e != nil {
			return nil, e
		}
		b, e := summarize(after)
		if e != nil {
			return nil, e
		}
		if a != b {
			return nil, fmt.Errorf("unexpected standard migration content: %s", name)
		}
		checks[name] = b
	}
	for _, q := range []string{
		`SELECT count(*) FROM ((SELECT table_schema,table_name FROM information_schema.tables WHERE table_catalog='candidate' AND table_type='BASE TABLE' AND NOT(table_schema='fundamental' AND table_name='field') EXCEPT SELECT table_schema,table_name FROM information_schema.tables WHERE table_catalog='baseline' AND table_type='BASE TABLE') UNION ALL (SELECT table_schema,table_name FROM information_schema.tables WHERE table_catalog='baseline' AND table_type='BASE TABLE' EXCEPT SELECT table_schema,table_name FROM information_schema.tables WHERE table_catalog='candidate' AND table_type='BASE TABLE'))`,
		`SELECT count(*) FROM ((SELECT DISTINCT canonical_field,unit,value_kind,period_basis FROM candidate.fundamental.provider_field WHERE canonical_field IS NOT NULL EXCEPT ALL SELECT * FROM candidate.fundamental.field) UNION ALL (SELECT * FROM candidate.fundamental.field EXCEPT ALL SELECT DISTINCT canonical_field,unit,value_kind,period_basis FROM candidate.fundamental.provider_field WHERE canonical_field IS NOT NULL))`,
		`SELECT abs(count(*)-46) FROM candidate.meta.schema_version`,
	} {
		var n int
		if err = db.QueryRowContext(ctx, q).Scan(&n); err != nil {
			return nil, err
		}
		if n != 0 {
			return nil, fmt.Errorf("standard catalogue/schema mismatch: %s", q)
		}
	}
	checks["fundamental.field"], err = summarize("SELECT * FROM candidate.fundamental.field")
	return checks, err
}
