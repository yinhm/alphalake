package duckdb

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"errors"
	"fmt"
	"regexp"
	"time"

	"github.com/yinhm/alphalake/internal/source/damodaran"
)

// PublishCountryRisk publishes one complete selected-country snapshot. It never
// overwrites observations or infers supersession from arrival order. The checkpoint
// is scoped to this artifact content, not an implicit global 'latest' pointer.
func PublishCountryRisk(ctx context.Context, db *sql.DB, runID, artifactID int64, parserHash string, s damodaran.Snapshot) (releaseID int64, inserted bool, err error) {
	if err = damodaran.Validate(s); err != nil {
		return
	}
	if !regexp.MustCompile(`^[0-9a-f]{64}$`).MatchString(parserHash) {
		return 0, false, errors.New("invalid parser digest")
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return
	}
	defer tx.Rollback()
	var hash, source, dataset, locator, status, runSource, runDataset string
	var firstSeen time.Time
	err = tx.QueryRowContext(ctx, `SELECT sha256,source,dataset,source_locator,fetched_at FROM meta.artifact WHERE artifact_id=?`, artifactID).Scan(&hash, &source, &dataset, &locator, &firstSeen)
	if err != nil {
		return
	}
	err = tx.QueryRowContext(ctx, `SELECT source,dataset,status FROM meta.ingest_run WHERE ingest_run_id=?`, runID).Scan(&runSource, &runDataset, &status)
	if err != nil {
		return
	}
	if hash != s.WorkbookSHA256 || source != damodaran.Source || dataset != damodaran.Dataset || locator != damodaran.URL || runSource != source || runDataset != dataset || status != IngestRunRunning {
		return 0, false, errors.New("country-risk artifact/run lineage mismatch")
	}
	observationDate, _ := time.Parse("2006-01-02", s.ObservationDate)
	if observationDate.After(firstSeen) {
		return 0, false, errors.New("observation date exceeds first seen")
	}
	// The parser code/runtime and normalization contract are part of interpretation.
	normalization := "country-risk-decimal12-v1;" + parserHash + ";" + s.Runtime
	digest := sha256.Sum256([]byte(hash + "\n" + s.ParserVersion + "\n" + normalization))
	key := hex.EncodeToString(digest[:])
	err = tx.QueryRowContext(ctx, `SELECT release_id FROM meta.dataset_release WHERE source=? AND dataset=? AND content_key=?`, source, dataset, key).Scan(&releaseID)
	if err == nil {
		var linked int
		err = tx.QueryRowContext(ctx, `SELECT count(*) FROM meta.dataset_release r
		 JOIN meta.dataset_release_artifact a ON a.release_id=r.release_id AND a.artifact_id=? AND a.role='data'
		 JOIN meta.checkpoint c ON c.source=r.source AND c.dataset=r.dataset AND c.checkpoint_key=r.content_key AND c.checkpoint_value=CAST(r.release_id AS VARCHAR)
		 WHERE r.release_id=? AND r.source_version=? AND r.parser_version=? AND r.normalization_version=?
		 AND r.availability_basis='first_seen' AND r.available_at=? AND r.first_seen_at=?`, artifactID, releaseID, s.ObservationDate, s.ParserVersion, normalization, firstSeen, firstSeen).Scan(&linked)
		if err != nil {
			return
		}
		if linked != 1 {
			return 0, false, errors.New("published country-risk lineage/checkpoint changed")
		}
		// Replaying an interpretation must agree with all previously published rows.
		var count int
		if err = tx.QueryRowContext(ctx, `SELECT count(*) FROM reference.country_risk WHERE release_id=?`, releaseID).Scan(&count); err != nil {
			return
		}
		if count != len(s.Observations) {
			return 0, false, errors.New("published country-risk row count changed")
		}
		for _, o := range s.Observations {
			var n int
			err = tx.QueryRowContext(ctx, `SELECT count(*) FROM reference.country_risk WHERE release_id=? AND artifact_id=? AND subject_kind=? AND subject_code=? AND metric_code=? AND observation_date=? AND method_code=? AND value_status='reported' AND raw_unit='fraction' AND source_locator=? AND raw_value=? AND value=CAST(? AS DECIMAL(38,12))`, releaseID, artifactID, o.SubjectKind, o.SubjectCode, o.MetricCode, s.ObservationDate, damodaran.Method(o), o.SourceLocator, o.RawValue, o.Value).Scan(&n)
			if err != nil {
				return
			}
			if n != 1 {
				return 0, false, errors.New("published country-risk interpretation changed")
			}
		}
		return releaseID, false, tx.Commit()
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return
	}
	err = tx.QueryRowContext(ctx, `INSERT INTO meta.dataset_release (source,dataset,source_version,content_key,publication_precision,available_at,availability_basis,first_seen_at,parser_version,normalization_version,ingest_run_id)
 VALUES (?,?,?,?,'unknown',?,'first_seen',?,?,?,?) RETURNING release_id`, source, dataset, s.ObservationDate, key, firstSeen, firstSeen, s.ParserVersion, normalization, runID).Scan(&releaseID)
	if err != nil {
		return
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO meta.dataset_release_artifact VALUES (?,?,'data')`, releaseID, artifactID); err != nil {
		return
	}
	for _, o := range s.Observations {
		_, err = tx.ExecContext(ctx, `INSERT INTO reference.country_risk (release_id,artifact_id,source_locator,raw_value,raw_unit,subject_kind,subject_code,observation_date,metric_code,method_code,value,value_status) VALUES (?,?,?,?,'fraction',?,?,?,?,?,CAST(? AS DECIMAL(38,12)),'reported')`, releaseID, artifactID, o.SourceLocator, o.RawValue, o.SubjectKind, o.SubjectCode, s.ObservationDate, o.MetricCode, damodaran.Method(o), o.Value)
		if err != nil {
			return
		}
	}
	_, err = tx.ExecContext(ctx, `INSERT INTO meta.checkpoint (source,dataset,checkpoint_key,checkpoint_value) VALUES (?,?,?,?) ON CONFLICT(source,dataset,checkpoint_key) DO UPDATE SET checkpoint_value=excluded.checkpoint_value,updated_at=now()`, source, dataset, key, fmt.Sprint(releaseID))
	if err != nil {
		return
	}
	err = tx.Commit()
	return releaseID, err == nil, err
}
