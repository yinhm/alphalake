package duckdb

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestSupplementReviewRevisionRevocation(t *testing.T) {
	ctx := t.Context()
	path := filepath.Join(t.TempDir(), "reviews.duckdb")
	db, err := OpenAndMigrate(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { db.Close() }()
	hash := strings.Repeat("a", 64)
	_, err = db.ExecContext(ctx, `INSERT INTO meta.artifact(artifact_id,source,dataset,source_locator,fetched_at,sha256,content_length) VALUES(1,'cninfo','filing_document','test',now(),?,1)`, hash)
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.ExecContext(ctx, `INSERT INTO fundamental.filing(filing_id,instrument_id,source,source_filing_id,provider_code,report_period,announcement_time,artifact_id,sha256)
 VALUES(1,1,'cninfo','old','000001','2025-12-31','2026-03-01',1,?),(2,1,'cninfo','new','000001','2025-12-31','2026-04-01',1,?);`, hash, hash)
	if err != nil {
		t.Fatal(err)
	}
	// 合成 finance_subsidiary 补充用于独立测试审核选择，不伪造标准财务。
	r := ReviewedSupplement{Code: "000001", Period: "2025-12-31", Item: "test_amount", Value: "10", Unit: "CNY", PeriodBasis: "instant", Scope: "finance_subsidiary", AnnouncementID: "old", PDFSHA256: hash, PDFPage: 1, Reviewer: "reviewer", ReviewNote: "test"}
	digest := func(v ReviewedSupplement) string {
		raw, _ := json.Marshal(v)
		return fmt.Sprintf("%x", sha256.Sum256(raw))
	}
	apply := func(v ReviewedSupplement, want int) {
		t.Helper()
		n, e := ImportReviewedSupplements(ctx, db, []ReviewedSupplement{v})
		if e != nil || n != want {
			t.Fatalf("apply %s: %d %v", v.Action, n, e)
		}
	}
	end := time.Date(2025, 12, 31, 0, 0, 0, 0, time.UTC)
	asof := time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC)
	value := func(want string) {
		t.Helper()
		out, e := ExportValuationData(ctx, db, "000001", end, asof)
		if e != nil {
			t.Fatal(e)
		}
		var rows []map[string]any
		if e = json.Unmarshal(out["supplements"].(json.RawMessage), &rows); e != nil {
			t.Fatal(e)
		}
		if want == "" {
			if len(rows) != 0 {
				t.Fatal("revocation fell back", rows)
			}
			return
		}
		if len(rows) != 1 || rows[0]["value"] != want {
			t.Fatal(rows, want)
		}
	}
	apply(r, 1)
	r.AnnouncementID = "new"
	apply(r, 1)
	original := r
	r.Action = "replace"
	r.SupersedesSHA256 = digest(original)
	r.ReviewedAt = "2026-09-01T00:00:00Z"
	r.Value = "11"
	r.ReviewNote = "correct extraction"
	bad := r
	bad.SupersedesSHA256 = hash
	if _, err = ImportReviewedSupplements(ctx, db, []ReviewedSupplement{bad}); err == nil {
		t.Fatal("wrong predecessor accepted")
	}
	apply(r, 1)
	value("11.0000000000")
	replacement := r
	r.Action = "revoke"
	r.SupersedesSHA256 = digest(replacement)
	r.ReviewNote = "withdraw review"
	bad = r
	bad.Value = "12"
	if _, err = ImportReviewedSupplements(ctx, db, []ReviewedSupplement{bad}); err == nil {
		t.Fatal("revocation changed amount")
	}
	apply(r, 1)
	value("")
	apply(original, 0)
	apply(replacement, 0)
	apply(r, 0)
	value("")
	if _, err = ImportReviewedSupplements(ctx, db, []ReviewedSupplement{bad}); err == nil {
		t.Fatal("stale predecessor accepted")
	}
	// 新动作加坏动作整个批次回滚。
	restore := r
	restore.Action = "replace"
	restore.SupersedesSHA256 = digest(r)
	restore.ReviewNote = "new review restores input"
	if _, err = ImportReviewedSupplements(ctx, db, []ReviewedSupplement{restore, bad}); err == nil {
		t.Fatal("bad batch accepted")
	}
	value("")
	if err = db.Close(); err != nil {
		t.Fatal(err)
	}
	db, err = OpenAndMigrate(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	value("")
	history, err := ExportSupplementReviewHistory(ctx, db, "000001")
	if err != nil {
		t.Fatal(err)
	}
	var events []map[string]any
	if err = json.Unmarshal(history, &events); err != nil {
		t.Fatal(err)
	}
	if len(events) != 4 {
		t.Fatal("lost or partially published history", string(history))
	}
	apply(restore, 1)
	value("11.0000000000")
}
