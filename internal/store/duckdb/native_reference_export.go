package duckdb

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"github.com/yinhm/alphalake/internal/source/damodaran"
	"time"
)

// NativeReferenceExport is a single consistent reference snapshot for the original backend.
// The snapshot is selected by availability, never by local file modification time.
func NativeReferenceExport(ctx context.Context, db *sql.DB, asof time.Time) (map[string]any, error) {
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	datasets := []string{damodaran.Dataset, damodaran.CompanyIndustryDataset}
	for _, stem := range damodaran.NativeReferenceFiles {
		datasets = append(datasets, damodaran.NativeReferenceDataset(stem))
	}
	ids := []int64{}
	for _, dataset := range datasets {
		var id int64
		err = tx.QueryRowContext(ctx, `SELECT r.release_id FROM meta.dataset_release r JOIN meta.ingest_run run ON run.ingest_run_id=r.ingest_run_id AND run.status='completed' JOIN meta.checkpoint c ON c.source=r.source AND c.dataset=r.dataset AND c.checkpoint_key=r.content_key AND c.checkpoint_value=CAST(r.release_id AS VARCHAR) WHERE r.source='damodaran' AND r.dataset=? AND r.available_at<=? ORDER BY r.source_version DESC NULLS LAST,r.recorded_at DESC,r.release_id DESC LIMIT 1`, dataset, asof).Scan(&id)
		if err != nil {
			return nil, fmt.Errorf("missing published native reference %s: %w", dataset, err)
		}
		ids = append(ids, id)
	}
	idsJSON, _ := json.Marshal(ids)
	selected := `SELECT unnest(CAST(? AS BIGINT[]))`
	queries := map[string]string{
		"releases":       `SELECT CAST(to_json(t) AS VARCHAR) FROM (SELECT r.*,a.artifact_id,a.sha256,a.source_locator,a.local_path FROM meta.dataset_release r JOIN meta.dataset_release_artifact l ON r.release_id=l.release_id AND l.role='data' JOIN meta.artifact a ON a.artifact_id=l.artifact_id WHERE r.release_id IN (` + selected + `) ORDER BY r.release_id) t`,
		"industry_stats": `SELECT CAST(to_json(t) AS VARCHAR) FROM (SELECT s.*,n.name AS industry FROM reference.industry_stat s JOIN classification.node n ON s.industry_node_id=n.node_id WHERE s.release_id IN (` + selected + `) ORDER BY s.release_id,n.name,s.metric_code) t`,
		"country_tax":    `SELECT CAST(to_json(t) AS VARCHAR) FROM (SELECT * FROM reference.country_tax WHERE release_id IN (` + selected + `) ORDER BY release_id,subject_code) t`,
		"country_risk":   `SELECT CAST(to_json(t) AS VARCHAR) FROM (SELECT * FROM reference.risk_observation WHERE release_id IN (` + selected + `) ORDER BY release_id,subject_code,metric_code) t`,
		"companies":      `SELECT CAST(to_json(t) AS VARCHAR) FROM (SELECT * FROM reference.security_industry WHERE release_id IN (` + selected + `) ORDER BY exchange_ticker) t`,
	}
	out := map[string]any{"contract": "alphalake-native-references-v2", "information_as_of": asof.Format(time.RFC3339Nano)}
	for key, query := range queries {
		rows, err := tx.QueryContext(ctx, query, string(idsJSON))
		if err != nil {
			return nil, err
		}
		values := []json.RawMessage{}
		for rows.Next() {
			var body string
			if err = rows.Scan(&body); err != nil {
				rows.Close()
				return nil, err
			}
			values = append(values, json.RawMessage(body))
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return nil, err
		}
		out[key] = values
	}
	releaseID, sha, err := issuerCompanyRelease(ctx, tx, string(idsJSON))
	if err != nil {
		return nil, err
	}
	associations, err := issuerIndustryAssociations(ctx, tx, releaseID, sha, asof)
	if err != nil {
		return nil, err
	}
	out["issuer_associations"] = associations
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	return out, nil
}
