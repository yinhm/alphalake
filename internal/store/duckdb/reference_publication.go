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
)

// Shared by the reviewed reference feeds, not a public arbitrary-table writer.
type referenceInput struct {
	Source, Dataset, URL, Date, SHA, ParserVersion, Runtime, Normalization, ParserHash string
}
type referencePublication struct {
	tx                   *sql.Tx
	id                   int64
	existing             bool
	source, dataset, key string
}

type referenceEvidence struct {
	ArtifactID     int64
	Role, URL, SHA string
}

func beginReferencePublication(ctx context.Context, db *sql.DB, runID, artifactID int64, in referenceInput, supporting ...referenceEvidence) (p referencePublication, err error) {
	if !regexp.MustCompile(`^[0-9a-f]{64}$`).MatchString(in.ParserHash) {
		return p, errors.New("invalid parser digest")
	}
	p.tx, err = db.BeginTx(ctx, nil)
	if err != nil {
		return
	}
	defer func() {
		if err != nil {
			p.tx.Rollback()
		}
	}()
	var hash, source, dataset, locator, status, runSource, runDataset string
	var firstSeen time.Time
	err = p.tx.QueryRowContext(ctx, `SELECT sha256,source,dataset,source_locator,fetched_at FROM meta.artifact WHERE artifact_id=?`, artifactID).Scan(&hash, &source, &dataset, &locator, &firstSeen)
	if err != nil {
		return
	}
	err = p.tx.QueryRowContext(ctx, `SELECT source,dataset,status FROM meta.ingest_run WHERE ingest_run_id=?`, runID).Scan(&runSource, &runDataset, &status)
	if err != nil {
		return
	}
	if hash != in.SHA || source != in.Source || dataset != in.Dataset || locator != in.URL || runSource != source || runDataset != dataset || status != IngestRunRunning {
		return p, errors.New("reference artifact/run lineage mismatch")
	}
	ids := map[int64]bool{artifactID: true}
	for _, proof := range supporting {
		if proof.ArtifactID <= 0 || ids[proof.ArtifactID] || (proof.Role != "publication" && proof.Role != "timing") {
			return p, errors.New("invalid or duplicate supporting reference artifact")
		}
		var proofHash, proofSource, proofDataset, proofURL string
		var fetched time.Time
		if err = p.tx.QueryRowContext(ctx, `SELECT sha256,source,dataset,source_locator,fetched_at FROM meta.artifact WHERE artifact_id=?`, proof.ArtifactID).Scan(&proofHash, &proofSource, &proofDataset, &proofURL, &fetched); err != nil {
			return
		}
		if proofHash != proof.SHA || proofSource != source || proofDataset != dataset || proofURL != proof.URL {
			return p, errors.New("supporting reference artifact lineage mismatch")
		}
		if fetched.After(firstSeen) {
			firstSeen = fetched
		}
		ids[proof.ArtifactID] = true
	}
	// 未标日期的来源保留 NULL 版本；各具体解析器仍负责其日期契约。
	var version any
	if in.Date != "" {
		date, dateErr := time.Parse("2006-01-02", in.Date)
		if dateErr != nil || date.After(firstSeen) {
			return p, errors.New("invalid observation date or exceeds first seen")
		}
		version = in.Date
	}
	normalization := in.Normalization + ";" + in.ParserHash + ";" + in.Runtime
	digest := sha256.Sum256([]byte(hash + "\n" + in.ParserVersion + "\n" + normalization))
	p.source, p.dataset, p.key = source, dataset, hex.EncodeToString(digest[:])
	err = p.tx.QueryRowContext(ctx, `SELECT release_id FROM meta.dataset_release WHERE source=? AND dataset=? AND content_key=?`, source, dataset, p.key).Scan(&p.id)
	if err == nil {
		p.existing = true
		var linked int
		err = p.tx.QueryRowContext(ctx, `SELECT count(*) FROM meta.dataset_release r
   JOIN meta.dataset_release_artifact a ON a.release_id=r.release_id AND a.artifact_id=? AND a.role='data'
   JOIN meta.checkpoint c ON c.source=r.source AND c.dataset=r.dataset AND c.checkpoint_key=r.content_key AND c.checkpoint_value=CAST(r.release_id AS VARCHAR)
   WHERE r.release_id=? AND r.source_version IS NOT DISTINCT FROM ? AND r.parser_version=? AND r.normalization_version=?
   AND r.publication_precision='unknown' AND r.source_published_at IS NULL
   AND r.availability_basis='first_seen' AND r.available_at=? AND r.first_seen_at=?`, artifactID, p.id, version, in.ParserVersion, normalization, firstSeen, firstSeen).Scan(&linked)
		if err == nil && linked != 1 {
			err = errors.New("published reference lineage/checkpoint changed")
		}
		if err != nil {
			return
		}
		var total int
		if err = p.tx.QueryRowContext(ctx, `SELECT count(*) FROM meta.dataset_release_artifact WHERE release_id=?`, p.id).Scan(&total); err != nil {
			return
		}
		if total != 1+len(supporting) {
			return p, errors.New("published reference evidence scope changed")
		}
		for _, proof := range supporting {
			if err = p.tx.QueryRowContext(ctx, `SELECT count(*) FROM meta.dataset_release_artifact WHERE release_id=? AND artifact_id=? AND role=?`, p.id, proof.ArtifactID, proof.Role).Scan(&linked); err != nil {
				return
			}
			if linked != 1 {
				return p, errors.New("published supporting reference link changed")
			}
		}
		return
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return
	}
	err = p.tx.QueryRowContext(ctx, `INSERT INTO meta.dataset_release(source,dataset,source_version,content_key,publication_precision,available_at,availability_basis,first_seen_at,parser_version,normalization_version,ingest_run_id)
 VALUES (?,?,?,?,'unknown',?,'first_seen',?,?,?,?) RETURNING release_id`, source, dataset, version, p.key, firstSeen, firstSeen, in.ParserVersion, normalization, runID).Scan(&p.id)
	if err != nil {
		return
	}
	_, err = p.tx.ExecContext(ctx, `INSERT INTO meta.dataset_release_artifact VALUES (?,?,'data')`, p.id, artifactID)
	if err != nil {
		return
	}
	for _, proof := range supporting {
		if _, err = p.tx.ExecContext(ctx, `INSERT INTO meta.dataset_release_artifact VALUES (?,?,?)`, p.id, proof.ArtifactID, proof.Role); err != nil {
			return
		}
	}
	return
}
func (p referencePublication) finish(ctx context.Context) (int64, bool, error) {
	if !p.existing {
		_, err := p.tx.ExecContext(ctx, `INSERT INTO meta.checkpoint(source,dataset,checkpoint_key,checkpoint_value) VALUES (?,?,?,?)`, p.source, p.dataset, p.key, fmt.Sprint(p.id))
		if err != nil {
			return 0, false, err
		}
	}
	err := p.tx.Commit()
	return p.id, !p.existing && err == nil, err
}
