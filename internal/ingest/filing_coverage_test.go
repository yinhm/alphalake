package ingest

import (
	"encoding/json"
	"math"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/artifact"
	"github.com/yinhm/alphalake/internal/domain"
	store "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestProspectusCoverageSourceOnlyAndRevocation(t *testing.T) {
	ctx := t.Context()
	root := t.TempDir()
	db, err := store.OpenInitialized(ctx, filepath.Join(root, "coverage.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	must := func(e error) {
		t.Helper()
		if e != nil {
			t.Fatal(e)
		}
	}
	ann := time.Date(2024, 6, 6, 16, 0, 0, 0, time.UTC)
	period := time.Date(2023, 12, 31, 0, 0, 0, 0, time.UTC)
	url := "https://static.cninfo.com.cn/finalpage/2024-06-06/test.PDF"
	pdf, err := artifact.Persist(ctx, db, root, artifact.Input{Source: "cninfo", Dataset: "filing_document", SourceLocator: url, FetchedAt: ann, MediaType: "application/pdf", ParserVersion: "test", Content: []byte("%PDF-1.4\nsynthetic plumbing fixture\n%%EOF")})
	must(err)
	_, err = db.ExecContext(ctx, `INSERT INTO fundamental.filing(filing_id,instrument_id,source,source_filing_id,provider_code,filing_type,filing_variant,announcement_time,artifact_id,sha256,source_url) VALUES(1,1,'cninfo','ipo','688692','prospectus','full',?,?,?,?)`, ann, pdf.ArtifactID, pdf.SHA256, url)
	must(err)
	source, err := artifact.Persist(ctx, db, root, artifact.Input{Source: "tdx", Dataset: "financial", SourceLocator: "test.zip", FetchedAt: ann.Add(time.Hour), MediaType: "application/zip", ParserVersion: "test", Content: []byte("source locator fixture")})
	must(err)
	_, err = db.ExecContext(ctx, `INSERT INTO fundamental.source_record VALUES(1,?,1,'688692',1,584,?,1)`, source.ArtifactID, period)
	must(err)
	review := FilingCoverageReview{Code: "688692", AnnouncementID: "ipo", Period: "2023-12-31", Fields: []string{"research_and_development_expense"}, PDFSHA256: pdf.SHA256, Pages: []int{1}, Reviewer: "test", Note: "synthetic scope test, no PDF numerical replacement", ReviewedAt: ann.Add(2 * time.Hour), Action: "publish"}
	for _, kind := range []string{"hash", "issuer", "period", "field", "future"} {
		bad := review
		switch kind {
		case "hash":
			bad.PDFSHA256 = "wrong"
		case "issuer":
			bad.Code = "001378"
		case "period":
			bad.Period = "2024-12-31"
		case "field":
			bad.Fields = []string{"FN304"}
		case "future":
			bad.ReviewedAt = time.Now().Add(time.Hour)
		}
		if _, err = ImportFilingCoverage(ctx, db, root, bad); err == nil {
			t.Fatal("accepted", kind)
		}
	}
	inserted, err := ImportFilingCoverage(ctx, db, root, review)
	must(err)
	if !inserted {
		t.Fatal("not inserted")
	}
	inserted, err = ImportFilingCoverage(ctx, db, root, review)
	must(err)
	if inserted {
		t.Fatal("not idempotent")
	}
	fields, err := store.LoadSnapshotFields(ctx, db)
	must(err)
	values := make([]domain.ProviderFloat32, 584)
	// Source-maintenance fixture positions, never exposed as business fields.
	values[303] = domain.ProviderFloat32{Value: 165131648, Bits: math.Float32bits(165131648)}
	values[73] = domain.ProviderFloat32{Value: 999, Bits: math.Float32bits(999)}
	record := store.IndexedFinancialRecord{ID: 1, Revision: source.SHA256, Record: domain.ProviderFinancialRecord{InstrumentID: 1, Provider: "tdx", ProviderCode: "688692", ReportPeriod: period, ProviderFields: values}}
	materialize := func() store.CanonicalFundamentalResult {
		t.Helper()
		_, e := store.RefreshProviderFilingLinks(ctx, db, 1, "tdx")
		must(e)
		conn, e := db.Conn(ctx)
		must(e)
		defer conn.Close()
		_, e = conn.ExecContext(ctx, "BEGIN")
		must(e)
		result, e := store.MaterializeFinancialSnapshotBatch(ctx, conn, 1, fields, []store.IndexedFinancialRecord{record})
		if e != nil {
			conn.ExecContext(ctx, "ROLLBACK")
			t.Fatal(e)
		}
		_, e = conn.ExecContext(ctx, "COMMIT")
		must(e)
		return result
	}
	result := materialize()
	if result.Inserted != 1 || result.Candidates != 1 {
		t.Fatal(result)
	}
	var count int
	var value float64
	must(db.QueryRowContext(ctx, `SELECT count(*),max(value) FROM fundamental.financial_observations('688692',NULL,NULL,NULL)`).Scan(&count, &value))
	if count != 1 || value != 165131648 {
		t.Fatal(count, value)
	}
	must(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.financial_observations('688692',NULL,NULL,?)`, ann.Add(-time.Second)).Scan(&count))
	if count != 0 {
		t.Fatal("pre-disclosure fact")
	}
	result = materialize()
	if result.Inserted+result.Updated+result.Removed != 0 {
		t.Fatal(result)
	}
	raw, err := json.Marshal(review)
	must(err)
	var prior string
	must(db.QueryRowContext(ctx, `SELECT sha256(?)`, string(raw)).Scan(&prior))
	review.Action = "revoke"
	review.Supersedes = prior
	review.Note = "withdraw disclosure coverage"
	_, err = ImportFilingCoverage(ctx, db, root, review)
	must(err)
	result = materialize()
	if result.Removed != 1 {
		t.Fatal(result)
	}
	// Disclosure coverage consumes the approved field-level zero policy.
	raw, err = json.Marshal(review)
	must(err)
	must(db.QueryRowContext(ctx, `SELECT sha256(?)`, string(raw)).Scan(&prior))
	review.Action = "publish"
	review.Supersedes = prior
	review.Note = "scope restored"
	_, err = ImportFilingCoverage(ctx, db, root, review)
	must(err)
	record.Record.ProviderFields[303] = domain.ProviderFloat32{}
	result = materialize()
	if result.Materialized != 1 || result.Rejected != 0 {
		t.Fatal(result)
	}
	must(db.QueryRowContext(ctx, `SELECT max(value) FROM fundamental.financial_observations('688692',NULL,NULL,NULL)`).Scan(&value))
	if value != 0 {
		t.Fatal("approved source zero", value)
	}
	// Coverage and field approval never create a value for an absent source position.
	record.Record.ProviderFields = record.Record.ProviderFields[:303]
	result = materialize()
	must(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.financial_observations('688692',NULL,NULL,NULL)`).Scan(&count))
	if result.Materialized != 0 || count != 0 {
		t.Fatal("absent source field became zero", result, count)
	}
}
