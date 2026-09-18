package main

import (
	"context"
	"database/sql"
	"fmt"
	"path/filepath"
)

// Fixed acceptance for the five reviewed asset components, not a generic diff allowlist.
func checkReviewedAssets(ctx context.Context, db *sql.DB, root string) (map[string]summary, error) {
	rows, err := db.QueryContext(ctx, `SELECT table_schema,table_name FROM information_schema.tables WHERE table_catalog='baseline' AND table_type='BASE TABLE' ORDER BY 1,2`)
	if err != nil {
		return nil, err
	}
	var tables [][2]string
	for rows.Next() {
		var t [2]string
		if err = rows.Scan(&t[0], &t[1]); err != nil {
			rows.Close()
			return nil, err
		}
		tables = append(tables, t)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return nil, err
	}
	checks := map[string]summary{}
	summarize := func(q string) (summary, error) {
		var s summary
		e := db.QueryRowContext(ctx, `SELECT count(*),CAST(coalesce(bit_xor(hash(t)),0) AS VARCHAR),CAST(coalesce(sum(CAST(hash(t) AS HUGEINT)),0) AS VARCHAR) FROM (`+q+`) t`).Scan(&s.Rows, &s.XOR, &s.Sum)
		return s, e
	}
	additions := map[string]int64{"meta.artifact": 3, "fundamental.filing_document": 2, "fundamental.document_review": 1, "fundamental.reviewed_supplement": 5, "fundamental.supplement_review_history": 5}
	keys := map[string]string{"meta.artifact": "artifact_id", "fundamental.filing_document": "filing_id,artifact_id", "fundamental.document_review": "filing_id,document_artifact_id", "fundamental.reviewed_supplement": "provider_code,report_period,item,source_filing_id", "fundamental.supplement_review_history": "import_sha256"}
	for _, t := range tables {
		name := t[0] + "." + t[1]
		old := "SELECT * FROM baseline." + name
		next := "SELECT * FROM candidate." + name
		if name == "fundamental.filing" {
			// All identity/timing fields are invariant; only these two missing documents may attach.
			projection := "* EXCLUDE(artifact_id,sha256)"
			old = "SELECT " + projection + " FROM baseline." + name
			next = "SELECT " + projection + " FROM candidate." + name
		}
		if n, ok := additions[name]; ok {
			var delta int64
			if err = db.QueryRowContext(ctx, "SELECT (SELECT count(*) FROM candidate."+name+")-(SELECT count(*) FROM baseline."+name+")").Scan(&delta); err != nil {
				return nil, err
			}
			if delta != n {
				return nil, fmt.Errorf("%s expected +%d got %d", name, n, delta)
			}
			next += " WHERE (" + keys[name] + ") IN (SELECT " + keys[name] + " FROM baseline." + name + ")"
		}
		a, e := summarize(old)
		if e != nil {
			return nil, e
		}
		b, e := summarize(next)
		if e != nil {
			return nil, e
		}
		if a != b {
			return nil, fmt.Errorf("%s unexpected content change", name)
		}
		checks[name] = a
	}
	// Refuse changes outside the two exact empty filing slots.
	for _, q := range []string{
		`SELECT count(*) FROM candidate.fundamental.filing_document d LEFT JOIN candidate.fundamental.filing f ON f.filing_id=d.filing_id LEFT JOIN candidate.meta.artifact a ON a.artifact_id=d.artifact_id WHERE (d.filing_id,d.artifact_id) NOT IN (SELECT filing_id,artifact_id FROM baseline.fundamental.filing_document) AND (f.artifact_id IS DISTINCT FROM d.artifact_id OR f.sha256 IS DISTINCT FROM d.sha256 OR a.sha256 IS DISTINCT FROM d.sha256 OR a.dataset IS DISTINCT FROM 'filing_document' OR a.source_locator IS DISTINCT FROM d.source_url OR NOT ((f.provider_code='300866' AND a.source='cninfo' AND d.source_url=f.source_url) OR (f.provider_code='002032' AND a.source='sina_issuer_announcement_mirror')))`,
		`SELECT count(*) FROM candidate.fundamental.document_review r LEFT JOIN candidate.meta.artifact a ON a.artifact_id=r.review_artifact_id LEFT JOIN candidate.fundamental.filing f ON f.filing_id=r.filing_id WHERE (r.filing_id,r.document_artifact_id) NOT IN (SELECT filing_id,document_artifact_id FROM baseline.fundamental.document_review) AND (a.sha256 IS DISTINCT FROM sha256(r.reviewed_record) OR a.source IS DISTINCT FROM 'document-review' OR a.dataset IS DISTINCT FROM 'filing_document_binding' OR f.artifact_id IS DISTINCT FROM r.document_artifact_id OR f.sha256 IS DISTINCT FROM r.pdf_sha256 OR f.provider_code IS DISTINCT FROM '002032')`,

		`SELECT count(*) FROM baseline.fundamental.filing b JOIN candidate.fundamental.filing c USING(filing_id) WHERE (b.artifact_id IS DISTINCT FROM c.artifact_id OR b.sha256 IS DISTINCT FROM c.sha256) AND NOT (b.artifact_id IS NULL AND b.sha256 IS NULL AND b.source='cninfo' AND b.report_period=DATE '2026-06-30' AND ((b.provider_code='300866' AND b.source_filing_id='1225533054' AND c.sha256='ff81b9e7c2e8eb04bd450fce3c084b28f5b4be4c1e2638250160b7bb813ebac1') OR (b.provider_code='002032' AND b.source_filing_id='1225522987' AND c.sha256='7460d1d9513785afd2596cea30d6a33b2a1a524089b77cec862222dd2d86ff45')))`,
		`SELECT count(*) FROM candidate.fundamental.reviewed_supplement s WHERE NOT EXISTS(SELECT 1 FROM baseline.fundamental.reviewed_supplement b WHERE b.import_sha256=s.import_sha256) AND (s.review_state<>'active' OR s.report_period<>DATE '2026-06-30' OR (s.provider_code,s.item,CAST(s.value AS VARCHAR)) NOT IN (('300866','reviewed_associate_investments','556090434.3000000000'),('002032','reviewed_current_total','1823720958.9000000000'),('002032','reviewed_current_restricted','750000000.0000000000'),('002032','reviewed_noncurrent_total','967473301.3800000000'),('002032','reviewed_noncurrent_restricted','20000000.0000000000')))`,
		`SELECT count(*) FROM candidate.fundamental.reviewed_supplement s LEFT JOIN candidate.fundamental.filing f ON f.filing_id=s.source_filing_id WHERE s.import_sha256 NOT IN (SELECT import_sha256 FROM baseline.fundamental.reviewed_supplement) AND (f.sha256 IS DISTINCT FROM s.pdf_sha256 OR f.provider_code IS DISTINCT FROM s.provider_code OR f.report_period IS DISTINCT FROM s.report_period)`,
		`SELECT count(*) FROM candidate.fundamental.supplement_review_history h WHERE h.import_sha256 NOT IN (SELECT import_sha256 FROM baseline.fundamental.supplement_review_history) AND (h.action<>'publish' OR h.supersedes_sha256 IS NOT NULL OR h.recorded_at IS NULL OR NOT EXISTS(SELECT 1 FROM candidate.fundamental.reviewed_supplement s WHERE s.import_sha256=h.import_sha256 AND s.reviewed_record=h.reviewed_record))`,
	} {
		var n int
		if err = db.QueryRowContext(ctx, q).Scan(&n); err != nil {
			return nil, err
		}
		if n != 0 {
			return nil, fmt.Errorf("invalid new evidence: %s", q)
		}
	}
	rows, err = db.QueryContext(ctx, `SELECT local_path,sha256 FROM candidate.meta.artifact WHERE artifact_id NOT IN (SELECT artifact_id FROM baseline.meta.artifact)`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var p, h string
		if err = rows.Scan(&p, &h); err != nil {
			return nil, err
		}
		if !filepath.IsLocal(p) {
			return nil, fmt.Errorf("nonlocal artifact")
		}
		actual, e := digest(filepath.Join(root, p))
		if e != nil {
			return nil, e
		}
		if actual != h {
			return nil, fmt.Errorf("artifact hash mismatch: %s", p)
		}
	}
	return checks, rows.Err()
}
