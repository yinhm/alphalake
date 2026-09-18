package ingest

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"fmt"
	"net/url"
	"time"

	"github.com/yinhm/alphalake/internal/artifact"
)

// ArchivedCNINFODocument describes a previously downloaded canonical document.
// The receipt asserts retrieval provenance; a matching hash alone does not.
type ArchivedCNINFODocument struct {
	Code           string    `json:"code"`
	Period         string    `json:"period"`
	AnnouncementID string    `json:"announcement_id"`
	URL            string    `json:"url"`
	SHA256         string    `json:"sha256"`
	FetchedAt      time.Time `json:"fetched_at"`
}

// ImportArchivedCNINFODocument fills only an existing resolved filing's empty
// document slot. It neither invents filing identities nor advances checkpoints.
func ImportArchivedCNINFODocument(ctx context.Context, db *sql.DB, root string, r ArchivedCNINFODocument, content []byte) (bool, error) {
	u, err := url.Parse(r.URL)
	if err != nil || u.Scheme != "https" || u.Host != "static.cninfo.com.cn" || u.User != nil || r.FetchedAt.IsZero() || r.FetchedAt.After(time.Now()) {
		return false, fmt.Errorf("canonical URL and valid original retrieval time required")
	}
	if fmt.Sprintf("%x", sha256.Sum256(content)) != r.SHA256 {
		return false, fmt.Errorf("archived PDF hash differs")
	}
	if err = validateCNINFOFilingDocument(r.URL, "application/pdf", content); err != nil {
		return false, err
	}
	var id int64
	var prior sql.NullInt64
	var hash sql.NullString
	err = db.QueryRowContext(ctx, `SELECT filing_id,artifact_id,sha256 FROM fundamental.filing WHERE source='cninfo' AND source_filing_id=? AND provider_code=? AND report_period=CAST(? AS DATE) AND source_url=? AND resolution_status='resolved' AND instrument_id IS NOT NULL AND announcement_time IS NOT NULL`, r.AnnouncementID, r.Code, r.Period, r.URL).Scan(&id, &prior, &hash)
	if err != nil {
		return false, fmt.Errorf("resolved canonical filing: %w", err)
	}
	if prior.Valid || hash.Valid {
		var n int
		err = db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.filing_document d JOIN meta.artifact a ON a.artifact_id=d.artifact_id WHERE d.filing_id=? AND d.artifact_id=? AND d.sha256=? AND a.sha256=? AND a.source='cninfo' AND a.dataset='filing_document' AND a.source_locator=?`, id, prior.Int64, r.SHA256, r.SHA256, r.URL).Scan(&n)
		if err != nil {
			return false, err
		}
		if n != 1 || hash.String != r.SHA256 {
			return false, fmt.Errorf("refusing to replace existing document provenance")
		}
	}
	a, err := artifact.Persist(ctx, db, root, artifact.Input{Source: "cninfo", Dataset: "filing_document", SourceLocator: r.URL, FetchedAt: r.FetchedAt, MediaType: "application/pdf", ParserVersion: "raw-document-v1", Content: content})
	if err != nil {
		return false, err
	}
	if prior.Valid {
		if prior.Int64 != a.ArtifactID {
			return false, fmt.Errorf("artifact identity changed")
		}
		return false, nil
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return false, err
	}
	defer tx.Rollback()
	var updated int64
	err = tx.QueryRowContext(ctx, `UPDATE fundamental.filing SET artifact_id=?,sha256=? WHERE filing_id=? AND artifact_id IS NULL AND sha256 IS NULL RETURNING filing_id`, a.ArtifactID, a.SHA256, id).Scan(&updated)
	if err != nil {
		return false, err
	}
	_, err = tx.ExecContext(ctx, `INSERT INTO fundamental.filing_document(filing_id,artifact_id,source_url,sha256,fetched_at) VALUES (?,?,?,?,?)`, id, a.ArtifactID, r.URL, a.SHA256, r.FetchedAt)
	if err != nil {
		return false, err
	}
	return true, tx.Commit()
}
