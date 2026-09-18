package main

import (
	"context"
	"database/sql"
	"fmt"
	"path/filepath"
	"strings"

	duck "github.com/yinhm/alphalake/internal/store/duckdb"
)

// 本次只准追加FN110事实及安克两期原文/零值审核；旧事实整行摘要必须不变。
func checkDisposalPublication(ctx context.Context, db *sql.DB, root string) (map[string]summary, error) {
	failCount := func(q string) error {
		var n int
		if e := db.QueryRowContext(ctx, q).Scan(&n); e != nil {
			return e
		}
		if n != 0 {
			return fmt.Errorf("unexpected disposal publication rows (%d): %s", n, q)
		}
		return nil
	}
	if e := failCount(`SELECT count(*) FROM baseline.fundamental.fact WHERE source_provider_field='FN110'`); e != nil {
		return nil, e
	}
	rows, e := db.QueryContext(ctx, `SELECT table_schema||'.'||table_name FROM information_schema.tables WHERE table_catalog='baseline' AND table_type='BASE TABLE' ORDER BY 1`)
	if e != nil {
		return nil, e
	}
	var tables []string
	for rows.Next() {
		var n string
		if e = rows.Scan(&n); e != nil {
			rows.Close()
			return nil, e
		}
		tables = append(tables, n)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return nil, e
	}
	keys := map[string]string{"meta.schema_version": "version", "fundamental.provider_field": "source,provider_field,valid_from", "fundamental.fact": "fact_id", "meta.ingest_run": "ingest_run_id", "meta.validation_result": "validation_result_id", "meta.artifact": "artifact_id", "fundamental.filing_document": "filing_id,artifact_id", "fundamental.reviewed_supplement": "provider_code,report_period,item,source_filing_id", "fundamental.supplement_review_history": "import_sha256"}
	expected := map[string]int64{"meta.schema_version": 1, "fundamental.provider_field": 1, "meta.artifact": 2, "fundamental.filing_document": 2, "fundamental.reviewed_supplement": 2, "fundamental.supplement_review_history": 2}
	checks := map[string]summary{}
	summarize := func(q string) (summary, error) {
		var s summary
		e := db.QueryRowContext(ctx, `SELECT count(*),CAST(coalesce(bit_xor(hash(t)),0) AS VARCHAR),CAST(coalesce(sum(CAST(hash(t) AS HUGEINT)),0) AS VARCHAR) FROM (`+q+`) t`).Scan(&s.Rows, &s.XOR, &s.Sum)
		return s, e
	}
	for _, name := range tables {
		before := "SELECT * FROM baseline." + name
		after := "SELECT * FROM candidate." + name
		if name == "fundamental.filing" {
			before = "SELECT * EXCLUDE(artifact_id,sha256) FROM baseline." + name
			after = "SELECT * EXCLUDE(artifact_id,sha256) FROM candidate." + name
		}
		if key, ok := keys[name]; ok {
			all, err := summarize("SELECT * FROM candidate." + name)
			if err != nil {
				return nil, err
			}
			old, err := summarize(before)
			if err != nil {
				return nil, err
			}
			delta := all.Rows - old.Rows
			if want, fixed := expected[name]; (fixed && delta != want) || (!fixed && delta <= 0) {
				return nil, fmt.Errorf("%s unexpected row delta %d", name, delta)
			}
			checks["after:"+name] = all
			after += " WHERE (" + key + ") IN (SELECT " + key + " FROM baseline." + name + ")"
		}
		a, err := summarize(before)
		if err != nil {
			return nil, err
		}
		b, err := summarize(after)
		if err != nil {
			return nil, err
		}
		if a != b {
			return nil, fmt.Errorf("old %s content changed", name)
		}
		checks[name] = a
	}
	// 精确重建目录迁移；不按当前实现猜倍率/期间。
	if _, e = db.ExecContext(ctx, `CREATE TEMP TABLE expected_field AS SELECT * FROM baseline.fundamental.provider_field`); e != nil {
		return nil, e
	}
	raw, e := duck.Read("045_asset_disposal_cash.sql")
	if e != nil {
		return nil, e
	}
	// 只在内存执行固定迁移文本，正式库全程READ_ONLY。
	if _, e = db.ExecContext(ctx, strings.ReplaceAll(string(raw), "fundamental.provider_field", "temp.main.expected_field")); e != nil {
		return nil, e
	}
	for _, q := range []string{
		`SELECT count(*) FROM ((SELECT table_schema,table_name FROM information_schema.tables WHERE table_catalog='baseline' AND table_type='BASE TABLE' EXCEPT SELECT table_schema,table_name FROM information_schema.tables WHERE table_catalog='candidate' AND table_type='BASE TABLE') UNION ALL (SELECT table_schema,table_name FROM information_schema.tables WHERE table_catalog='candidate' AND table_type='BASE TABLE' EXCEPT SELECT table_schema,table_name FROM information_schema.tables WHERE table_catalog='baseline' AND table_type='BASE TABLE'))`,
		`SELECT count(*) FROM candidate.fundamental.provider_fact p JOIN candidate.fundamental.provider_filing_link l ON l.provider_source=p.source AND l.provider_revision_key=p.revision_key AND l.provider_code=p.provider_code JOIN candidate.fundamental.filing f ON f.filing_id=l.filing_id WHERE p.source='tdx' AND p.provider_field='FN110' AND p.report_period>=DATE '2025-01-01' AND isfinite(p.value) AND p.value<>0 AND l.status='linked' AND f.resolution_status='resolved' AND p.instrument_id=f.instrument_id AND p.report_period=f.report_period AND f.announcement_time>=p.report_period AND f.filing_type=CASE month(p.report_period) WHEN 3 THEN 'quarterly_q1' WHEN 6 THEN 'semiannual' WHEN 9 THEN 'quarterly_q3' WHEN 12 THEN 'annual' END AND NOT EXISTS(SELECT 1 FROM candidate.fundamental.fact v WHERE v.provider_fact_id=p.provider_fact_id AND v.source_filing_id=f.filing_id AND v.source_provider_field='FN110')`,
		`SELECT count(*) FROM candidate.fundamental.reviewed_supplement s WHERE s.item='reviewed_asset_disposal_cash_zero' AND s.import_sha256 NOT IN (SELECT import_sha256 FROM baseline.fundamental.reviewed_supplement) AND (NOT EXISTS(SELECT 1 FROM candidate.fundamental.provider_fact p JOIN candidate.fundamental.provider_filing_link l ON l.provider_source=p.source AND l.provider_revision_key=p.revision_key AND l.provider_code=p.provider_code WHERE p.provider_code=s.provider_code AND p.report_period=s.report_period AND p.provider_field='FN110' AND p.value=0 AND l.filing_id=s.source_filing_id AND l.status='linked') OR EXISTS(SELECT 1 FROM candidate.fundamental.fact f WHERE f.provider_code=s.provider_code AND f.report_period=s.report_period AND f.source_provider_field='FN110'))`,

		`SELECT count(*) FROM ((SELECT * FROM expected_field EXCEPT ALL SELECT * FROM candidate.fundamental.provider_field) UNION ALL (SELECT * FROM candidate.fundamental.provider_field EXCEPT ALL SELECT * FROM expected_field))`,
		`SELECT count(*) FROM candidate.meta.schema_version WHERE version NOT IN (SELECT version FROM baseline.meta.schema_version) AND version<>45`,
		`SELECT count(*) FROM candidate.fundamental.fact f LEFT JOIN candidate.fundamental.provider_fact p ON p.provider_fact_id=f.provider_fact_id LEFT JOIN candidate.fundamental.filing fi ON fi.filing_id=f.source_filing_id WHERE f.fact_id NOT IN (SELECT fact_id FROM baseline.fundamental.fact) AND (f.source_provider_field IS DISTINCT FROM 'FN110' OR f.primary_source IS DISTINCT FROM 'tdx' OR f.canonical_field IS DISTINCT FROM 'long_lived_asset_disposal_cash' OR p.provider_field IS DISTINCT FROM 'FN110' OR p.source IS DISTINCT FROM 'tdx' OR f.value IS DISTINCT FROM CAST(CAST(p.value AS VARCHAR) AS DECIMAL(38,10)) OR f.value=0 OR f.unit IS DISTINCT FROM 'CNY' OR f.statement_scope IS DISTINCT FROM 'provider_default' OR f.report_period IS DISTINCT FROM p.report_period OR f.report_period<DATE '2025-01-01' OR f.report_period IS DISTINCT FROM fi.report_period OR f.instrument_id IS DISTINCT FROM p.instrument_id OR f.instrument_id IS DISTINCT FROM fi.instrument_id OR f.provider_code IS DISTINCT FROM p.provider_code OR f.revision_key IS DISTINCT FROM p.revision_key OR f.announcement_time IS DISTINCT FROM fi.announcement_time OR f.period_type IS DISTINCT FROM CASE month(f.report_period) WHEN 3 THEN 'Q1' WHEN 6 THEN 'H1' WHEN 9 THEN '9M' WHEN 12 THEN 'FY' END)`,
		`SELECT count(*) FROM candidate.meta.ingest_run WHERE ingest_run_id NOT IN (SELECT ingest_run_id FROM baseline.meta.ingest_run) AND (source<>'alphalake' OR dataset<>'fundamental_fact' OR status NOT IN ('completed','partial') OR finished_at IS NULL)`,
		`SELECT count(*) FROM candidate.meta.validation_result v LEFT JOIN candidate.fundamental.provider_fact p ON CAST(p.provider_fact_id AS VARCHAR)=v.subject_key WHERE v.validation_result_id NOT IN (SELECT validation_result_id FROM baseline.meta.validation_result) AND (v.source<>'alphalake' OR v.dataset<>'fundamental_fact' OR v.subject_type<>'provider_fact' OR p.provider_field IS DISTINCT FROM 'FN110' OR v.passed OR v.rule_code IS DISTINCT FROM 'provider_zero_ambiguous' OR p.value IS DISTINCT FROM 0)`,
		`SELECT count(*) FROM baseline.fundamental.filing b JOIN candidate.fundamental.filing c USING(filing_id) WHERE (b.artifact_id IS DISTINCT FROM c.artifact_id OR b.sha256 IS DISTINCT FROM c.sha256) AND NOT (b.artifact_id IS NULL AND b.sha256 IS NULL AND b.source='cninfo' AND b.provider_code='300866' AND ((b.source_filing_id='1224614835' AND c.sha256='e1aff2dcb92aea9bcc8b3b69b66051b2e27c68026dbbff77d56ef27373dee674') OR (b.source_filing_id='1225090973' AND c.sha256='8dadc4bfd7282738697a5ee82d9b25faa657e053e3f480e71dd6ae9987703202')))`,
		`SELECT count(*) FROM candidate.fundamental.filing_document d LEFT JOIN candidate.fundamental.filing f ON f.filing_id=d.filing_id LEFT JOIN candidate.meta.artifact a ON a.artifact_id=d.artifact_id WHERE (d.filing_id,d.artifact_id) NOT IN (SELECT filing_id,artifact_id FROM baseline.fundamental.filing_document) AND (f.artifact_id IS DISTINCT FROM d.artifact_id OR f.sha256 IS DISTINCT FROM d.sha256 OR a.sha256 IS DISTINCT FROM d.sha256 OR a.source IS DISTINCT FROM 'cninfo' OR a.dataset IS DISTINCT FROM 'filing_document' OR d.source_url IS DISTINCT FROM f.source_url OR a.source_locator IS DISTINCT FROM f.source_url OR f.provider_code IS DISTINCT FROM '300866')`,
		`SELECT count(*) FROM candidate.fundamental.reviewed_supplement s LEFT JOIN candidate.fundamental.filing f ON f.filing_id=s.source_filing_id WHERE s.import_sha256 NOT IN (SELECT import_sha256 FROM baseline.fundamental.reviewed_supplement) AND (s.provider_code<>'300866' OR s.item<>'reviewed_asset_disposal_cash_zero' OR s.report_period NOT IN (DATE '2025-06-30',DATE '2025-12-31') OR s.value<>0 OR s.unit<>'CNY' OR s.period_basis<>'ytd' OR s.statement_scope<>'consolidated_note_component' OR s.review_state<>'active' OR s.pdf_sha256 IS DISTINCT FROM f.sha256 OR s.report_period IS DISTINCT FROM f.report_period OR f.provider_code IS DISTINCT FROM s.provider_code OR s.import_sha256 IS DISTINCT FROM sha256(s.reviewed_record))`,
		`SELECT count(*) FROM candidate.fundamental.supplement_review_history h WHERE h.import_sha256 NOT IN (SELECT import_sha256 FROM baseline.fundamental.supplement_review_history) AND (h.action<>'publish' OR h.supersedes_sha256 IS NOT NULL OR h.recorded_at IS NULL OR h.reviewed_at IS NULL OR NOT EXISTS(SELECT 1 FROM candidate.fundamental.reviewed_supplement s WHERE s.import_sha256=h.import_sha256 AND s.reviewed_record=h.reviewed_record))`,
	} {
		if e = failCount(q); e != nil {
			return nil, e
		}
	}
	rows, e = db.QueryContext(ctx, `SELECT local_path,sha256 FROM candidate.meta.artifact WHERE artifact_id NOT IN (SELECT artifact_id FROM baseline.meta.artifact)`)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	for rows.Next() {
		var p, h string
		if e = rows.Scan(&p, &h); e != nil {
			return nil, e
		}
		if !filepath.IsLocal(p) {
			return nil, fmt.Errorf("nonlocal raw path")
		}
		actual, err := digest(filepath.Join(root, p))
		if err != nil {
			return nil, err
		}
		if actual != h {
			return nil, fmt.Errorf("new PDF hash differs")
		}
	}
	return checks, rows.Err()
}
