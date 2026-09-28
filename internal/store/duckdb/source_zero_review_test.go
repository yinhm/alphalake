package duckdb

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

func TestReviewedSourceZeroExportAndRevocation(t *testing.T) {
	ctx := t.Context()
	root := t.TempDir()
	db, err := OpenInitialized(ctx, filepath.Join(root, "db.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	check := func(e error) {
		t.Helper()
		if e != nil {
			t.Fatal(e)
		}
	}
	id, err := UpsertInstrument(ctx, db, domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHE", Currency: "CNY"}, domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sz300866"})
	check(err)
	period := time.Date(2023, 12, 31, 0, 0, 0, 0, time.UTC)
	// Synthetic ZIP tests governance, not an independent real-amount validation.
	record, sha, sourcePath := archiveFinancialFixture(t, ctx, db, "300866", id, period, map[int]float32{8: 100})
	_, err = ReconcileFinancialSourceRecords(ctx, db, 1, "tdx", sha, []domain.ProviderFinancialRecord{record})
	check(err)
	pdf, err := os.ReadFile("../../ingest/testdata/anker-history-2026/1219800919.pdf")
	check(err)
	pdfHash := fmt.Sprintf("%x", sha256.Sum256(pdf))
	check(os.WriteFile(filepath.Join(root, "annual.pdf"), pdf, 0600))
	var artifact int64
	check(db.QueryRowContext(ctx, `INSERT INTO meta.artifact(source,dataset,source_locator,fetched_at,sha256,content_length,local_path) VALUES('cninfo','filing_document','annual',now(),?,?,'annual.pdf') RETURNING artifact_id`, pdfHash, len(pdf)).Scan(&artifact))
	_, err = db.ExecContext(ctx, `INSERT INTO fundamental.filing(filing_id,instrument_id,source,source_filing_id,provider_code,report_period,announcement_time,filing_type,filing_variant,resolution_status,artifact_id,sha256) VALUES(1,?,'cninfo','1219800919','300866','2023-12-31','2024-04-25','annual','full','resolved',?,?)`, id, artifact, pdfHash)
	check(err)
	_, err = RefreshProviderFilingLinks(ctx, db, 1, "tdx")
	check(err)
	_, err = MaterializeCanonicalFundamentals(ctx, db, 2, "tdx")
	check(err)
	var mapping string
	check(db.QueryRowContext(ctx, `SELECT sha256(CAST(to_json(m) AS VARCHAR)) FROM fundamental.statement_field m WHERE canonical_field='bonds_payable' AND valid_to=DATE '2025-01-01'`).Scan(&mapping))
	r := ReviewedSupplement{Code: "300866", Period: "2023-12-31", Item: "reviewed_source_zero_bonds_payable", Value: "0", Unit: "CNY", PeriodBasis: "instant", Scope: "consolidated_statement", AnnouncementID: "1219800919", PDFSHA256: pdfHash, PDFPage: 173, Reviewer: "test", ReviewNote: "Explicit repayment and zero balance; synthetic source binding test", ReviewedAt: "2026-09-01T00:00:00Z", SourceZero: &ReviewedSourceZero{Field: "bonds_payable", ArtifactSHA256: sha, SourceRow: 1, MappingSHA256: mapping, Conclusion: "explicit_zero_balance"}}
	apply := func(v ReviewedSupplement, want int) {
		t.Helper()
		n, e := ImportReviewedSupplements(ctx, db, []ReviewedSupplement{v})
		check(e)
		if n != want {
			t.Fatal(n, want)
		}
	}
	export := func(want int) {
		t.Helper()
		dir := filepath.Join(t.TempDir(), "out")
		check(ExportFinancialSQLiteRows(ctx, db, dir, []string{"300866"}, []string{"bonds_payable"}, period, period, time.Now()))
		raw, e := os.ReadFile(filepath.Join(dir, "reviewed_zeros.jsonl"))
		check(e)
		if strings.Count(string(raw), "\n") != want {
			t.Fatal(string(raw), want)
		}
		facts, e := os.ReadFile(filepath.Join(dir, "facts.jsonl"))
		check(e)
		if len(facts) != 0 {
			t.Fatal("review leaked into standard facts", string(facts))
		}
	}
	export(0)
	for _, change := range []func(*ReviewedSupplement){func(v *ReviewedSupplement) { v.Value = "1" }, func(v *ReviewedSupplement) { v.SourceZero.Conclusion = "not_applicable" }, func(v *ReviewedSupplement) { v.SourceZero.SourceRow = 2 }, func(v *ReviewedSupplement) { v.SourceZero.MappingSHA256 = strings.Repeat("a", 64) }} {
		bad := r
		binding := *r.SourceZero
		bad.SourceZero = &binding
		change(&bad)
		if _, e := ImportReviewedSupplements(ctx, db, []ReviewedSupplement{bad}); e == nil {
			t.Fatal("invalid review accepted", bad)
		}
	}
	apply(r, 1)
	apply(r, 0)
	export(1)
	// Catalogue withdrawal invalidates the review without falling back to zero.
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET notes='withdrawn' WHERE canonical_field='bonds_payable' AND valid_to=DATE '2025-01-01'`)
	check(err)
	export(0)
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field p SET notes=m.notes FROM fundamental.statement_field m WHERE p.source=m.source AND p.provider_field=m.provider_field AND p.valid_from=m.valid_from`)
	check(err)
	export(1)
	raw, _ := json.Marshal(r)
	revoke := r
	revoke.Action = "revoke"
	revoke.SupersedesSHA256 = fmt.Sprintf("%x", sha256.Sum256(raw))
	revoke.ReviewNote = "withdraw evidence"
	apply(revoke, 1)
	apply(r, 0)
	export(0)
	raw, _ = json.Marshal(revoke)
	restore := revoke
	restore.Action = "replace"
	restore.SupersedesSHA256 = fmt.Sprintf("%x", sha256.Sum256(raw))
	restore.ReviewNote = "review restored"
	apply(restore, 1)
	export(1)
	check(db.Close())
	db, err = OpenInitialized(ctx, filepath.Join(root, "db.duckdb"))
	check(err)
	defer db.Close()
	export(1)
	_, err = db.ExecContext(ctx, `INSERT INTO fundamental.statement_snapshot SELECT s.* REPLACE(source_record_id+1 AS source_record_id,announcement_time+INTERVAL 1 SECOND AS announcement_time) FROM fundamental.statement_snapshot s`)
	check(err)
	export(0)
	_, err = db.ExecContext(ctx, `DELETE FROM fundamental.statement_snapshot WHERE source_record_id=(SELECT max(source_record_id) FROM fundamental.statement_snapshot)`)
	check(err)
	export(1)
	check(os.WriteFile(filepath.Join(root, "annual.pdf"), []byte("%PDF-corrupt"), 0600))
	if e := ExportFinancialSQLiteRows(ctx, db, filepath.Join(t.TempDir(), "bad-pdf"), []string{"300866"}, []string{"bonds_payable"}, period, period, time.Now()); e == nil {
		t.Fatal("corrupt PDF accepted")
	}
	check(os.WriteFile(filepath.Join(root, "annual.pdf"), pdf, 0600))
	check(os.WriteFile(sourcePath, []byte("corrupt"), 0600))
	if e := ExportFinancialSQLiteRows(ctx, db, filepath.Join(t.TempDir(), "bad"), []string{"300866"}, []string{"bonds_payable"}, period, period, time.Now()); e == nil {
		t.Fatal("corrupt source accepted")
	}
}
