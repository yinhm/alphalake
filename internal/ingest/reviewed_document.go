package ingest

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"regexp"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/artifact"
)

// ReviewedDocument binds an independently retrieved issuer document to an
// existing CNINFO identity. It does not assert byte identity with CNINFO.
type ReviewedDocument struct {
	Code            string    `json:"code"`
	Period          string    `json:"period"`
	AnnouncementID  string    `json:"announcement_id"`
	CanonicalURL    string    `json:"canonical_url"`
	RetrievalURL    string    `json:"retrieval_url"`
	RetrievalSource string    `json:"retrieval_source"`
	SHA256          string    `json:"sha256"`
	FetchedAt       time.Time `json:"fetched_at"`
	ReviewedAt      time.Time `json:"reviewed_at"`
	Reviewer        string    `json:"reviewer"`
	ReviewNote      string    `json:"review_note"`
}

// ImportReviewedDocument only fills a missing document. Raw bytes and the
// review are immutable artifacts; the selected filing, history and review
// association are published together. Failed imports may leave raw artifacts,
// but never a partially attached document or an advanced sync checkpoint.
func ImportReviewedDocument(ctx context.Context, db *sql.DB, root string, review ReviewedDocument, content []byte) (bool, error) {
	if !regexp.MustCompile(`^[0-9]{6}$`).MatchString(review.Code) || strings.TrimSpace(review.AnnouncementID) == "" || strings.TrimSpace(review.Reviewer) == "" || strings.TrimSpace(review.ReviewNote) == "" {
		return false, errors.New("document identity and explicit semantic review required")
	}
	if _, err := time.Parse("2006-01-02", review.Period); err != nil {
		return false, err
	}
	if review.FetchedAt.IsZero() || review.ReviewedAt.Before(review.FetchedAt) || review.ReviewedAt.After(time.Now()) {
		return false, errors.New("invalid retrieval/review times")
	}
	canonical, err := url.Parse(review.CanonicalURL)
	if err != nil || canonical.Scheme != "https" || canonical.Host != "static.cninfo.com.cn" || canonical.User != nil {
		return false, errors.New("canonical CNINFO URL required")
	}
	retrieval, err := url.Parse(review.RetrievalURL)
	if err != nil || retrieval.Scheme != "https" || retrieval.Host == "" || retrieval.User != nil || review.RetrievalURL == review.CanonicalURL || !regexp.MustCompile(`^[a-z][a-z0-9_-]*$`).MatchString(review.RetrievalSource) || review.RetrievalSource == "cninfo" {
		return false, errors.New("explicit distinct mirror provenance required")
	}
	if fmt.Sprintf("%x", sha256.Sum256(content)) != review.SHA256 {
		return false, errors.New("reviewed PDF hash differs")
	}
	if err := validateCNINFOFilingDocument(review.CanonicalURL, "application/pdf", content); err != nil {
		return false, err
	}
	var id int64
	var priorArtifact sql.NullInt64
	var priorHash sql.NullString
	err = db.QueryRowContext(ctx, `SELECT filing_id,artifact_id,sha256 FROM fundamental.filing WHERE source='cninfo' AND source_filing_id=? AND provider_code=? AND report_period=CAST(? AS DATE) AND source_url=? AND resolution_status='resolved' AND instrument_id IS NOT NULL AND announcement_time IS NOT NULL`, review.AnnouncementID, review.Code, review.Period, review.CanonicalURL).Scan(&id, &priorArtifact, &priorHash)
	if err != nil {
		return false, fmt.Errorf("no unique resolved canonical filing: %w", err)
	}
	raw, err := json.Marshal(review)
	if err != nil {
		return false, err
	}
	// Validate an existing attachment before persisting any new artifacts.
	alreadyAttached := false
	if priorArtifact.Valid || priorHash.Valid {
		var existing string
		err = db.QueryRowContext(ctx, `SELECT r.reviewed_record FROM fundamental.document_review r JOIN meta.artifact a ON a.artifact_id=r.review_artifact_id AND a.sha256=sha256(r.reviewed_record) AND a.source='document-review' AND a.dataset='filing_document_binding' WHERE r.filing_id=? AND r.document_artifact_id=? AND r.pdf_sha256=?`, id, priorArtifact.Int64, review.SHA256).Scan(&existing)
		if err != nil || existing != string(raw) || priorHash.String != review.SHA256 {
			return false, errors.New("refusing to replace an existing document or its review")
		}
		alreadyAttached = true
	}
	pdf, err := artifact.Persist(ctx, db, root, artifact.Input{Source: review.RetrievalSource, Dataset: "filing_document", SourceLocator: review.RetrievalURL, FetchedAt: review.FetchedAt, MediaType: "application/pdf", ParserVersion: "raw-document-v1", Content: content})
	if err != nil {
		return false, err
	}
	reviewArtifact, err := artifact.Persist(ctx, db, root, artifact.Input{Source: "document-review", Dataset: "filing_document_binding", SourceLocator: review.CanonicalURL, FetchedAt: review.ReviewedAt, MediaType: "application/json", ParserVersion: "reviewed-document-v1", Content: raw})
	if err != nil {
		return false, err
	}
	if alreadyAttached {
		if pdf.ArtifactID != priorArtifact.Int64 {
			return false, errors.New("reviewed artifact identity changed")
		}
		return false, nil
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return false, err
	}
	defer tx.Rollback()
	var updated int64
	err = tx.QueryRowContext(ctx, `UPDATE fundamental.filing SET artifact_id=?,sha256=? WHERE filing_id=? AND artifact_id IS NULL AND sha256 IS NULL RETURNING filing_id`, pdf.ArtifactID, pdf.SHA256, id).Scan(&updated)
	if err != nil {
		return false, fmt.Errorf("filing document changed during review: %w", err)
	}
	_, err = tx.ExecContext(ctx, `INSERT INTO fundamental.filing_document(filing_id,artifact_id,source_url,sha256,fetched_at) VALUES (?,?,?,?,?)`, id, pdf.ArtifactID, review.RetrievalURL, pdf.SHA256, review.FetchedAt)
	if err != nil {
		return false, err
	}
	_, err = tx.ExecContext(ctx, `INSERT INTO fundamental.document_review
 (filing_id,document_artifact_id,review_artifact_id,pdf_sha256,reviewed_at,recorded_at,reviewed_record)
 VALUES (?,?,?,?,?,current_timestamp,?)`, id, pdf.ArtifactID, reviewArtifact.ArtifactID, pdf.SHA256, review.ReviewedAt, string(raw))
	if err != nil {
		return false, err
	}
	if err = tx.Commit(); err != nil {
		return false, err
	}
	return true, nil
}
