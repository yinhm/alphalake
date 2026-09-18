package ingest

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestReviewedMirrorDocument(t *testing.T) {
	ctx := t.Context()
	root := t.TempDir()
	path := filepath.Join(root, "review.duckdb")
	db, err := duckstore.OpenAndMigrate(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { db.Close() }()
	dir := "../../valuation/research/reviewed-assets-20260917/supor"
	raw, err := os.ReadFile(filepath.Join(dir, "receipt.json"))
	if err != nil {
		t.Fatal(err)
	}
	var receipt struct {
		Code, Period, SHA256 string
		RetrievalURL         string    `json:"retrieval_url"`
		CanonicalURL         string    `json:"canonical_url"`
		RetrievedAt          time.Time `json:"retrieved_at"`
	}
	if err = json.Unmarshal(raw, &receipt); err != nil {
		t.Fatal(err)
	}
	pdf, err := os.ReadFile(filepath.Join(dir, "12563787.pdf"))
	if err != nil {
		t.Fatal(err)
	}
	review := ReviewedDocument{Code: receipt.Code, Period: receipt.Period, SHA256: receipt.SHA256, CanonicalURL: receipt.CanonicalURL, RetrievalURL: receipt.RetrievalURL, RetrievalSource: "sina", FetchedAt: receipt.RetrievedAt, ReviewedAt: receipt.RetrievedAt, AnnouncementID: "1225522987", Reviewer: "test-semantic-review", ReviewNote: "133页苏泊尔2026H1；第86/87页分类与金额已独立核对；未证明与CNINFO逐字节相同"}
	_, err = db.ExecContext(ctx, `INSERT INTO fundamental.filing(instrument_id,source,source_filing_id,provider_code,report_period,announcement_time,source_url) VALUES (1,'cninfo',?,'002032','2026-06-30','2026-08-28 16:00:00+00',?)`, review.AnnouncementID, review.CanonicalURL)
	if err != nil {
		t.Fatal(err)
	}
	for _, kind := range []string{"hash", "code", "period", "canonical", "source", "future", "pdf"} {
		bad := review
		content := pdf
		switch kind {
		case "hash":
			bad.SHA256 = "bad"
		case "code":
			bad.Code = "300866"
		case "period":
			bad.Period = "2025-06-30"
		case "canonical":
			bad.CanonicalURL += "?changed"
		case "source":
			bad.RetrievalSource = "cninfo"
		case "future":
			bad.ReviewedAt = time.Now().Add(time.Hour)
		case "pdf":
			content = []byte("<html>denied</html>")
			bad.SHA256 = fmt.Sprintf("%x", sha256.Sum256(content))
		}
		if _, err = ImportReviewedDocument(ctx, db, root, bad, content); err == nil {
			t.Fatalf("accepted %s", kind)
		}
	}
	if inserted, err := ImportReviewedDocument(ctx, db, root, review, pdf); err != nil || !inserted {
		t.Fatalf("attach %v %v", inserted, err)
	}
	if err = db.Close(); err != nil {
		t.Fatal(err)
	}
	db, err = duckstore.OpenAndMigrate(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	if inserted, err := ImportReviewedDocument(ctx, db, root, review, pdf); err != nil || inserted {
		t.Fatalf("replay %v %v", inserted, err)
	}
	var local string
	if err = db.QueryRowContext(ctx, `SELECT a.local_path FROM meta.artifact a JOIN fundamental.filing f ON f.artifact_id=a.artifact_id`).Scan(&local); err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(root, local), []byte("damaged cache"), 0600); err != nil {
		t.Fatal(err)
	}
	if inserted, err := ImportReviewedDocument(ctx, db, root, review, pdf); err != nil || inserted {
		t.Fatalf("repair replay %v %v", inserted, err)
	}
	repaired, err := os.ReadFile(filepath.Join(root, local))
	if err != nil || fmt.Sprintf("%x", sha256.Sum256(repaired)) != review.SHA256 {
		t.Fatal("cache not repaired")
	}
	changed := review
	changed.ReviewNote += " changed"
	if _, err = ImportReviewedDocument(ctx, db, root, changed, pdf); err == nil {
		t.Fatal("changed review overwrote selected document")
	}
	var source, locator, canonical, history string
	err = db.QueryRowContext(ctx, `SELECT a.source,a.source_locator,f.source_url,d.source_url FROM fundamental.filing f JOIN meta.artifact a ON a.artifact_id=f.artifact_id JOIN fundamental.filing_document d ON d.filing_id=f.filing_id`).Scan(&source, &locator, &canonical, &history)
	if err != nil {
		t.Fatal(err)
	}
	if source != "sina" || locator != review.RetrievalURL || history != locator || canonical != review.CanonicalURL {
		t.Fatalf("lost provenance: %s %s %s %s", source, locator, canonical, history)
	}
	var n int
	if err = db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.document_review`).Scan(&n); err != nil || n != 1 {
		t.Fatalf("review association %d %v", n, err)
	}
	// 清理诊断不影响正式审核及幂等导入。
	if _, err = db.ExecContext(ctx, `DELETE FROM meta.validation_result`); err != nil {
		t.Fatal(err)
	}
	if inserted, err := ImportReviewedDocument(ctx, db, root, review, pdf); err != nil || inserted {
		t.Fatalf("replay after diagnostics cleanup: %v %v", inserted, err)
	}
	supplement := duckstore.ReviewedSupplement{Code: review.Code, Period: review.Period, Item: "restricted_current_debt_investment", Value: "750000000", Unit: "CNY", PeriodBasis: "instant", Scope: "consolidated_note_component", AnnouncementID: review.AnnouncementID, PDFSHA256: review.SHA256, PDFPage: 86, Reviewer: review.Reviewer, ReviewNote: review.ReviewNote}
	if n, err := duckstore.ImportReviewedSupplements(ctx, db, []duckstore.ReviewedSupplement{supplement}); err != nil || n != 1 {
		t.Fatalf("supplement import %d %v", n, err)
	}
	// 将真实原文审核样本恢复成 schema40 的旧存储形态，再走正式迁移。
	legacy, err := json.Marshal(review)
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.ExecContext(ctx, `INSERT INTO meta.validation_result(source,dataset,rule_code,severity,subject_type,subject_key,passed,details,checked_at)
 SELECT 'document-review','filing_document','reviewed_mirror_binding','info','filing',CAST(filing_id AS VARCHAR),true,?,? FROM fundamental.filing`, string(legacy), review.ReviewedAt)
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.ExecContext(ctx, `DROP TABLE fundamental.document_review;
 DROP TABLE fundamental.supplement_review_history;
 ALTER TABLE fundamental.reviewed_supplement DROP COLUMN review_state;
 DELETE FROM meta.schema_version WHERE version IN (41,42);`)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = db.ExecContext(ctx, `UPDATE meta.validation_result SET details='{}' WHERE rule_code='reviewed_mirror_binding'`); err != nil {
		t.Fatal(err)
	}
	if err = duckstore.Apply(ctx, db); err == nil {
		t.Fatal("legacy review without archived evidence migrated")
	}
	if version, e := duckstore.CurrentSchemaVersion(ctx, db); e != nil || version != 40 {
		t.Fatalf("failed migration advanced version: %d %v", version, e)
	}
	if _, err = db.ExecContext(ctx, `UPDATE meta.validation_result SET details=? WHERE rule_code='reviewed_mirror_binding'`, string(legacy)); err != nil {
		t.Fatal(err)
	}
	if err = duckstore.Apply(ctx, db); err != nil {
		t.Fatal(err)
	}
	if err = duckstore.Apply(ctx, db); err != nil {
		t.Fatal(err)
	}
	if err = db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.document_review WHERE recorded_at IS NULL`).Scan(&n); err != nil || n != 1 {
		t.Fatalf("legacy review time fabricated: %d %v", n, err)
	}
	if err = db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.supplement_review_history WHERE recorded_at IS NULL AND reviewed_at IS NULL`).Scan(&n); err != nil || n != 1 {
		t.Fatalf("legacy supplement time fabricated: %d %v", n, err)
	}
	end := time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC)
	asof := time.Date(2026, 9, 18, 0, 0, 0, 0, time.UTC)
	before, err := duckstore.ExportValuationData(ctx, db, review.Code, end, asof)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = db.ExecContext(ctx, `DELETE FROM meta.validation_result`); err != nil {
		t.Fatal(err)
	}
	after, err := duckstore.ExportValuationData(ctx, db, review.Code, end, asof)
	if err != nil {
		t.Fatal(err)
	}
	beforeJSON, _ := json.Marshal(before)
	afterJSON, _ := json.Marshal(after)
	if string(beforeJSON) != string(afterJSON) {
		t.Fatal("diagnostic cleanup changed export")
	}
	if inserted, err := ImportReviewedDocument(ctx, db, root, review, pdf); err != nil || inserted {
		t.Fatalf("migrated replay: %v %v", inserted, err)
	}
	if _, err = db.ExecContext(ctx, `UPDATE fundamental.document_review SET pdf_sha256='bad'`); err != nil {
		t.Fatal(err)
	}
	if _, err = duckstore.ExportValuationData(ctx, db, review.Code, end, asof); err == nil {
		t.Fatal("mismatched document review accepted")
	}

}
