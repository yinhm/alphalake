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

// Shared by the three reviewed reference feeds, not a public arbitrary-table writer.
type referenceInput struct {
	Source, Dataset, URL, Date, SHA, ParserVersion, Runtime, Normalization, ParserHash string
}
type referencePublication struct {
	tx                   *sql.Tx
	id                   int64
	existing             bool
	source, dataset, key string
}

func beginReferencePublication(ctx context.Context, db *sql.DB, runID, artifactID int64, in referenceInput) (p referencePublication, err error) {
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
	date, dateErr := time.Parse("2006-01-02", in.Date)
	if dateErr != nil || date.After(firstSeen) {
		return p, errors.New("invalid observation date or exceeds first seen")
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
   WHERE r.release_id=? AND r.source_version=? AND r.parser_version=? AND r.normalization_version=?
   AND r.availability_basis='first_seen' AND r.available_at=? AND r.first_seen_at=?`, artifactID, p.id, in.Date, in.ParserVersion, normalization, firstSeen, firstSeen).Scan(&linked)
		if err == nil && linked != 1 {
			err = errors.New("published reference lineage/checkpoint changed")
		}
		return
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return
	}
	err = p.tx.QueryRowContext(ctx, `INSERT INTO meta.dataset_release(source,dataset,source_version,content_key,publication_precision,available_at,availability_basis,first_seen_at,parser_version,normalization_version,ingest_run_id)
 VALUES (?,?,?,?,'unknown',?,'first_seen',?,?,?,?) RETURNING release_id`, source, dataset, in.Date, p.key, firstSeen, firstSeen, in.ParserVersion, normalization, runID).Scan(&p.id)
	if err != nil {
		return
	}
	_, err = p.tx.ExecContext(ctx, `INSERT INTO meta.dataset_release_artifact VALUES (?,?,'data')`, p.id, artifactID)
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
