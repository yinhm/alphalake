package ingest

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/artifact"
)

// FilingCoverageReview records disclosure scope, never a replacement number.
// Every period is reviewed explicitly; a title or report date is insufficient.
type FilingCoverageReview struct {
	Code           string    `json:"code"`
	AnnouncementID string    `json:"announcement_id"`
	Period         string    `json:"period"`
	Fields         []string  `json:"fields"`
	PDFSHA256      string    `json:"pdf_sha256"`
	Pages          []int     `json:"pages"`
	Reviewer       string    `json:"reviewer"`
	Note           string    `json:"note"`
	ReviewedAt     time.Time `json:"reviewed_at"`
	Action         string    `json:"action"`
	Supersedes     string    `json:"supersedes,omitempty"`
}

func ImportFilingCoverage(ctx context.Context, db *sql.DB, root string, review FilingCoverageReview) (bool, error) {
	period, err := time.Parse("2006-01-02", review.Period)
	if err != nil || period.Month() != 12 || period.Day() != 31 {
		return false, errors.New("reviewed annual period required")
	}
	if len(review.Code) != 6 || strings.Trim(review.Code, "0123456789") != "" || review.AnnouncementID == "" || strings.TrimSpace(review.Reviewer) == "" || strings.TrimSpace(review.Note) == "" || review.ReviewedAt.IsZero() || review.ReviewedAt.After(time.Now()) {
		return false, errors.New("explicit issuer, period and semantic review required")
	}
	if review.Action != "publish" && review.Action != "revoke" {
		return false, errors.New("publish or revoke required")
	}
	if review.Action == "revoke" && review.Supersedes == "" {
		return false, errors.New("revocation requires predecessor")
	}
	if len(review.Fields) == 0 || len(review.Pages) == 0 {
		return false, errors.New("reviewed fields and PDF pages required")
	}
	sort.Strings(review.Fields)
	for i, name := range review.Fields {
		var count int
		if i > 0 && name == review.Fields[i-1] {
			return false, errors.New("duplicate field")
		}
		if err = db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.field WHERE canonical_field=? AND period_basis='ytd'`, name).Scan(&count); err != nil {
			return false, err
		}
		if count != 1 {
			return false, fmt.Errorf("unrecognized annual flow field %q", name)
		}
	}
	for _, page := range review.Pages {
		if page < 1 {
			return false, errors.New("PDF pages are one-based")
		}
	}
	var id int64
	var url, hash string
	var announcement time.Time
	err = db.QueryRowContext(ctx, `SELECT filing_id,source_url,sha256,announcement_time FROM fundamental.filing
 WHERE source='cninfo' AND source_filing_id=? AND provider_code=? AND filing_type='prospectus' AND filing_variant='full'
 AND resolution_status='resolved' AND instrument_id IS NOT NULL AND artifact_id IS NOT NULL`, review.AnnouncementID, review.Code).Scan(&id, &url, &hash, &announcement)
	if err != nil {
		return false, fmt.Errorf("resolved prospectus with archived PDF required: %w", err)
	}
	if hash != review.PDFSHA256 || announcement.Before(period) || review.ReviewedAt.Before(announcement) {
		return false, errors.New("PDF hash or disclosure/review period mismatch")
	}
	versions, _, err := artifact.LoadHealthyVersions(ctx, db, root, "cninfo", "filing_document", url, 0)
	if err != nil {
		return false, err
	}
	verified := false
	for _, v := range versions {
		if v.Stored.SHA256 == hash && validateCNINFOFilingDocument(url, "application/pdf", v.Content) == nil {
			verified = true
			break
		}
	}
	if !verified {
		return false, errors.New("prospectus bytes unavailable or hash-invalid")
	}
	raw, err := json.Marshal(review)
	if err != nil {
		return false, err
	}
	stored, err := artifact.Persist(ctx, db, root, artifact.Input{Source: "disclosure-review", Dataset: "filing_period_coverage", SourceLocator: review.Code + "/" + review.AnnouncementID + "/" + review.Period, FetchedAt: review.ReviewedAt, MediaType: "application/json", ParserVersion: "filing-coverage-review-v1", Content: raw})
	if err != nil {
		return false, err
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return false, err
	}
	defer tx.Rollback()
	var exists int
	if err = tx.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.filing_coverage_review WHERE review_sha256=?`, stored.SHA256).Scan(&exists); err != nil {
		return false, err
	}
	if exists == 1 {
		return false, nil
	}
	var previous sql.NullString
	err = tx.QueryRowContext(ctx, `SELECT max(r.review_sha256) FROM fundamental.filing_coverage_review r
 WHERE filing_id=? AND (reviewed_record::JSON->>'period')=?
 AND NOT EXISTS(SELECT 1 FROM fundamental.filing_coverage_review n WHERE n.supersedes_sha256=r.review_sha256)`, id, review.Period).Scan(&previous)
	if err != nil {
		return false, err
	}
	if previous.String != review.Supersedes {
		return false, errors.New("coverage predecessor changed; explicit replacement required")
	}
	_, err = tx.ExecContext(ctx, `INSERT INTO fundamental.filing_coverage_review VALUES (?,?,?,?,?)`, stored.SHA256, id, stored.ArtifactID, nullableCoverageHash(review.Supersedes), string(raw))
	if err != nil {
		return false, err
	}
	return true, tx.Commit()
}

func nullableCoverageHash(value string) any {
	if value == "" {
		return nil
	}
	return value
}
