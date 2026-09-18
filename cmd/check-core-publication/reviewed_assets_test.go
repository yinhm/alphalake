package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/yinhm/alphalake/internal/ingest"
	duck "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestReviewedAssetPublication(t *testing.T) {
	ctx := t.Context()
	root := t.TempDir()
	base := filepath.Join(root, "base.duckdb")
	candidate := filepath.Join(root, "candidate.duckdb")
	db, err := duck.OpenAndMigrate(ctx, base)
	if err != nil {
		t.Fatal(err)
	}
	must := func(e error) {
		t.Helper()
		if e != nil {
			t.Fatal(e)
		}
	}
	read := func(p string) []byte { t.Helper(); b, e := os.ReadFile(filepath.Join("../..", p)); must(e); return b }
	var receipts map[string]ingest.ArchivedCNINFODocument
	must(json.Unmarshal(read("internal/ingest/testdata/anker-valuation-2026/reports.json"), &receipts))
	a := receipts["1225533054"]
	a.Code = "300866"
	a.Period = "2026-06-30"
	a.AnnouncementID = "1225533054"
	var s ingest.ReviewedDocument
	must(json.Unmarshal(read("valuation/research/reviewed-assets-20260917/supor/document-review.json"), &s))
	for _, r := range [][3]string{{a.Code, a.AnnouncementID, a.URL}, {s.Code, s.AnnouncementID, s.CanonicalURL}} {
		_, err = db.ExecContext(ctx, `INSERT INTO fundamental.filing(instrument_id,source,source_filing_id,provider_code,report_period,announcement_time,source_url) VALUES (1,'cninfo',?,?,'2026-06-30','2026-08-31 16:00:00+00',?)`, r[1], r[0], r[2])
		must(err)
	}
	must(db.Close())
	b, err := os.ReadFile(base)
	must(err)
	must(os.WriteFile(candidate, b, 0600))
	db, err = duck.OpenAndMigrate(ctx, candidate)
	must(err)
	_, err = ingest.ImportArchivedCNINFODocument(ctx, db, root, a, read("internal/ingest/testdata/anker-valuation-2026/1225533054.pdf"))
	must(err)
	_, err = ingest.ImportReviewedDocument(ctx, db, root, s, read("valuation/research/reviewed-assets-20260917/supor/12563787.pdf"))
	must(err)
	for _, p := range []string{"internal/ingest/testdata/reviewed-assets-2026/supplements.json", "valuation/research/reviewed-assets-20260917/supor/supplements.json"} {
		var records []duck.ReviewedSupplement
		must(json.Unmarshal(read(p), &records))
		_, err = duck.ImportReviewedSupplements(ctx, db, records)
		must(err)
	}
	must(db.Close())
	args := []string{base, candidate, "--reviewed-assets", "--raw-root", root}
	must(run(args))
	db, err = duck.Open(ctx, candidate)
	must(err)
	_, err = db.ExecContext(ctx, `UPDATE fundamental.reviewed_supplement SET value=value+0.01 WHERE provider_code='300866'`)
	must(err)
	must(db.Close())
	if run(args) == nil {
		t.Fatal("accepted changed supplement")
	}
	db, err = duck.Open(ctx, candidate)
	must(err)
	_, err = db.ExecContext(ctx, `UPDATE fundamental.reviewed_supplement SET value=value-0.01 WHERE provider_code='300866'`)
	must(err)
	must(db.Close())
	bh, err := digest(base)
	must(err)
	ch, err := digest(candidate)
	must(err)
	must(run(append(args, "--publish", "--base-sha256", bh, "--candidate-sha256", ch)))
	backupHash, err := digest(base + ".pre-reviewed-assets-20260918")
	must(err)
	if backupHash != bh {
		t.Fatal("backup differs")
	}
	db, err = duck.OpenReadOnly(ctx, base)
	must(err)
	defer db.Close()
	var n int
	must(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.reviewed_supplement WHERE review_state='active'`).Scan(&n))
	if n != 5 {
		t.Fatal(n)
	}
}
