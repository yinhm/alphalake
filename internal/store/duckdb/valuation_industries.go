package duckdb

import (
	"context"
	"database/sql"
	"encoding/json"
	"time"
)

// Readiness and SQLite export must apply the same published classification evidence.
func valuationIndustriesAsOf(ctx context.Context, tx *sql.Tx, asof time.Time) (map[int64][]any, map[string]any, error) {
	members, reference, err := companyIndustriesAsOf(ctx, tx, asof)
	if err != nil {
		members = map[int64][]any{}
		reference["status"] = "blocked_invalid_reference"
		reference["reason"] = err.Error()
	}
	rows, err := tx.QueryContext(ctx, `SELECT instrument_id,CAST(to_json(industry_memberships) AS VARCHAR) FROM (

 SELECT m.instrument_id,list(struct_pack(source:=t.source,taxonomy_code:=t.taxonomy_code,
 node_code:=n.source_node_code,node_name:=n.name,node_id:=n.node_id,
 observed_at:=CAST(m.last_observed_at AS VARCHAR),effective_from:=CAST(m.effective_from AS VARCHAR),
 ingest_run_id:=m.last_observed_run_id,run_finished_at:=CAST(r.finished_at AS VARCHAR))
 ORDER BY t.taxonomy_code,n.source_node_code) AS industry_memberships
 FROM classification.membership m JOIN classification.node n USING(node_id)
 JOIN classification.taxonomy t USING(taxonomy_id) JOIN meta.ingest_run r ON r.ingest_run_id=m.last_observed_run_id
 WHERE t.taxonomy_type='industry' AND m.source=t.source AND r.source=m.source
 AND r.dataset='classification_industry' AND r.status IN ('completed','partial')
 AND m.last_observed_at<=CAST(? AS TIMESTAMPTZ) AND r.finished_at<=CAST(? AS TIMESTAMPTZ)
 AND m.effective_from<=CAST(? AS DATE) AND (m.effective_to IS NULL OR m.effective_to>CAST(? AS DATE))
 GROUP BY m.instrument_id
 )`, asof, asof, asof.In(time.FixedZone("China", 8*3600)).Format("2006-01-02"), asof.In(time.FixedZone("China", 8*3600)).Format("2006-01-02"))
	if err != nil {
		return nil, reference, err
	}
	defer rows.Close()
	for rows.Next() {
		var id int64
		var raw string
		if err = rows.Scan(&id, &raw); err != nil {
			return nil, reference, err
		}
		var observations []any
		if err = json.Unmarshal([]byte(raw), &observations); err != nil {
			return nil, reference, err
		}
		members[id] = append(members[id], observations...)
	}
	return members, reference, rows.Err()
}
