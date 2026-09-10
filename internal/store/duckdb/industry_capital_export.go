package duckdb

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/source/damodaran"
	"github.com/yinhm/alphalake/internal/source/reference"
)

// ExportIndustryCapital 固定完整参考版本；releaseID=0按观察日期/同日修订选择，不回退损坏版本。
func ExportIndustryCapital(ctx context.Context, db *sql.DB, asof time.Time, recorded *time.Time, releaseID int64) (map[string]any, error) {
	if db == nil || asof.IsZero() || releaseID < 0 {
		return nil, errors.New("database, ASOF and nonnegative release required")
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	rows, err := tx.QueryContext(ctx, `SELECT release_id FROM meta.dataset_release
 WHERE source=? AND dataset=? AND (?=0 OR release_id=?) AND available_at<=?
 AND (? IS NULL OR recorded_at<=CAST(? AS TIMESTAMPTZ)) AND TRY_CAST(source_version AS DATE)<=CAST(? AS DATE)
 QUALIFY dense_rank() OVER(ORDER BY TRY_CAST(source_version AS DATE) DESC,recorded_at DESC)=1`,
		damodaran.Source, damodaran.CapitalDataset, releaseID, releaseID, asof, recorded, recorded, asof)
	if err != nil {
		return nil, err
	}
	count := 0
	for rows.Next() {
		if err = rows.Scan(&releaseID); err != nil {
			rows.Close()
			return nil, err
		}
		count++
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return nil, err
	}
	if count != 1 {
		return nil, errors.New("missing or ambiguous industry capital release")
	}
	var releaseRaw string
	err = tx.QueryRowContext(ctx, `SELECT CAST(to_json(list(r)) AS VARCHAR) FROM (
 SELECT r.release_id,r.source,r.dataset,r.source_version,r.content_key,r.parser_version,r.normalization_version,
 CAST(r.available_at AS VARCHAR) AS available_at,CAST(r.recorded_at AS VARCHAR) AS recorded_at,r.availability_basis,
 a.artifact_id,a.sha256 AS artifact_sha256,a.source_locator AS source_url
 FROM meta.dataset_release r JOIN meta.dataset_release_artifact l ON l.release_id=r.release_id AND l.role='data'
 JOIN meta.artifact a ON a.artifact_id=l.artifact_id AND a.source=r.source AND a.dataset=r.dataset
 JOIN meta.checkpoint c ON c.source=r.source AND c.dataset=r.dataset AND c.checkpoint_key=r.content_key AND c.checkpoint_value=CAST(r.release_id AS VARCHAR)
 WHERE r.release_id=?) r`, releaseID).Scan(&releaseRaw)
	if err != nil {
		return nil, fmt.Errorf("capital release lineage: %w", err)
	}
	var releases []json.RawMessage
	if err = json.Unmarshal([]byte(releaseRaw), &releases); err != nil {
		return nil, err
	}
	if len(releases) != 1 {
		return nil, errors.New("broken capital release lineage")
	}
	var meta struct {
		Version       string `json:"source_version"`
		SHA           string `json:"artifact_sha256"`
		Parser        string `json:"parser_version"`
		Normalization string `json:"normalization_version"`
		Key           string `json:"content_key"`
		URL           string `json:"source_url"`
	}
	if err = json.Unmarshal(releases[0], &meta); err != nil {
		return nil, err
	}
	parts := strings.SplitN(meta.Normalization, ";", 3)
	if len(parts) != 3 || parts[0] != "global-capital-decimal12-v1" || len(parts[1]) != 64 || meta.URL != damodaran.CapitalURL || fmt.Sprintf("%x", sha256.Sum256([]byte(meta.SHA+"\n"+meta.Parser+"\n"+meta.Normalization))) != meta.Key {
		return nil, errors.New("capital release interpretation changed")
	}
	if _, err = hex.DecodeString(parts[1]); err != nil {
		return nil, errors.New("invalid capital parser digest")
	}
	var total int
	if err = tx.QueryRowContext(ctx, `SELECT count(*) FROM reference.industry_stat WHERE release_id=?`, releaseID).Scan(&total); err != nil {
		return nil, err
	}
	if total != 94 {
		return nil, errors.New("incomplete capital observations")
	}
	var observationsRaw string
	err = tx.QueryRowContext(ctx, `SELECT CAST(to_json(list(o ORDER BY industry)) AS VARCHAR) FROM (
 SELECT o.observation_id,o.release_id,o.artifact_id,n.source_node_code AS industry,o.sample_count,o.metric_code,
 o.method_code,o.sample_region,o.statistic_code,o.value_status,o.raw_unit,o.raw_value,o.source_locator,
 CAST(o.observation_date AS VARCHAR) AS observation_date,CAST(o.value AS VARCHAR) AS value
 FROM reference.industry_stat o JOIN classification.node n ON n.node_id=o.industry_node_id
 JOIN classification.taxonomy t ON t.taxonomy_id=n.taxonomy_id
 JOIN meta.dataset_release_artifact l ON l.release_id=o.release_id AND l.artifact_id=o.artifact_id AND l.role='data'
 WHERE o.release_id=? AND o.observation_date=CAST(? AS DATE) AND t.source='damodaran' AND t.taxonomy_code=?
 AND n.name=n.source_node_code AND o.metric_code=? AND o.method_code=? AND o.raw_unit='dimensionless'
 AND o.sample_region='global' AND o.statistic_code='provider_estimate' AND o.value_status='reported') o`,
		releaseID, meta.Version, damodaran.BetaTaxonomy, damodaran.CapitalMetric, damodaran.CapitalMethod).Scan(&observationsRaw)
	if err != nil {
		return nil, fmt.Errorf("capital observations: %w", err)
	}
	var observations []damodaran.IndustryObservation
	if err = json.Unmarshal([]byte(observationsRaw), &observations); err != nil {
		return nil, err
	}
	snapshot := damodaran.CapitalSnapshot{Header: reference.Header{Contract: "alphalake-global-capital-v1", ObservationDate: meta.Version, SHA256: meta.SHA, ParserVersion: meta.Parser, Runtime: parts[2]}, Observations: observations}
	if err = damodaran.ValidateCapital(snapshot); err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"contract_version": "alphalake-industry-capital-v1", "information_as_of": asof.UTC().Format(time.RFC3339Nano), "recorded_cutoff": recorded, "release": releases[0], "observations": json.RawMessage(observationsRaw)}, nil
}
