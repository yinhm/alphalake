package duckdb

import (
	"github.com/yinhm/alphalake/internal/domain"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestInsertProviderFinancialRecordsPreservesRawBitsAndRevisions(t *testing.T) {
	ctx := t.Context()
	db, e := OpenInitialized(ctx, filepath.Join(t.TempDir(), "source.duckdb"))
	if e != nil {
		t.Fatal(e)
	}
	defer db.Close()
	period := time.Date(2025, 12, 31, 0, 0, 0, 0, time.UTC)
	r, sha, path := archiveFinancialFixture(t, ctx, db, "300866", 1, period, map[int]float32{1: 123.5})
	first, e := ReconcileFinancialSourceRecords(ctx, db, 1, "tdx", sha, []domain.ProviderFinancialRecord{r})
	if e != nil || first.Inserted != 1 {
		t.Fatal(first, e)
	}
	replay, e := ReconcileFinancialSourceRecords(ctx, db, 2, "tdx", sha, []domain.ProviderFinancialRecord{r})
	if e != nil || replay.Inserted != 0 || replay.Reassigned != 0 {
		t.Fatal(replay, e)
	}
	source, e := ExportSourceFinancialData(ctx, db, "300866", period)
	if e != nil || len(source.Observations) != 584 {
		t.Fatal(len(source.Observations), e)
	}
	if source.Observations[0].Evidence.Bits == nil || *source.Observations[0].Evidence.Bits != r.ProviderFields[0].Bits {
		t.Fatal(source.Observations[0])
	}
	var n int
	e = db.QueryRowContext(ctx, `SELECT count(*) FROM information_schema.tables WHERE table_schema='fundamental' AND table_name IN('provider_fact','fact')`).Scan(&n)
	if e != nil || n != 0 {
		t.Fatal("redundant numeric layer", n, e)
	}
	if e = os.WriteFile(path, []byte("corrupt"), 0600); e != nil {
		t.Fatal(e)
	}
	if _, e = ExportSourceFinancialData(ctx, db, "300866", period); e == nil {
		t.Fatal("unverified source export")
	}
}
func TestProviderFactReconcileReassignsSameRevisionWithoutDuplicates(t *testing.T) {
	db, r, sha, _ := linkedFinancialFixture(t)
	ctx := t.Context()
	if _, e := MaterializeCanonicalFundamentals(ctx, db, 2, "tdx"); e != nil {
		t.Fatal(e)
	}
	r.InstrumentID = 2
	out, e := ReconcileFinancialSourceRecords(ctx, db, 3, "tdx", sha, []domain.ProviderFinancialRecord{r})
	if e != nil || out.Reassigned != 1 || out.Inserted != 0 {
		t.Fatal(out, e)
	}
	var n int
	e = db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.statement_snapshot`).Scan(&n)
	if e != nil || n != 0 {
		t.Fatal("stale standard row retained", n, e)
	}
	out, e = ReconcileFinancialSourceRecords(ctx, db, 4, "tdx", sha, nil)
	if e != nil || out.Removed != 1 {
		t.Fatal(out, e)
	}
	e = db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.source_record WHERE instrument_id IS NULL`).Scan(&n)
	if e != nil || n != 1 {
		t.Fatal("source locator lost", n, e)
	}
}
func TestProviderFactsRejectMissingSourceIdentityWithoutBackfill(t *testing.T) {
	db, r, sha, _ := linkedFinancialFixture(t)
	ctx := t.Context()
	r.SourceRow = 0
	if _, e := ReconcileFinancialSourceRecords(ctx, db, 2, "tdx", sha, []domain.ProviderFinancialRecord{r}); e == nil {
		t.Fatal("missing original row guessed")
	}
}
func TestProviderFactBatchesRollbackAndRemoveAbsentCodes(t *testing.T) {
	db, r, sha, _ := linkedFinancialFixture(t)
	ctx := t.Context()
	inputs := []ProviderFinancialResolutionInput{{ArtifactID: r.ArtifactID, Source: "tdx", SourceFile: r.SourceFile, ProviderCode: r.ProviderCode, ReportPeriod: r.ReportPeriod, InstrumentID: 2, IdentifierValue: "sz300866"}}
	invalid := r
	invalid.SourceRow = 0
	if _, _, e := PublishFinancialPackage(ctx, db, 2, sha, []domain.ProviderFinancialRecord{invalid}, inputs, "package", "md5"); e == nil {
		t.Fatal("invalid locator accepted")
	}
	var n int
	if e := db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.provider_record_resolution`).Scan(&n); e != nil || n != 0 {
		t.Fatal("governance published without locators", n, e)
	}
	if _, found, e := GetCheckpoint(ctx, db, "tdx", "professional_financial", "package"); e != nil || found {
		t.Fatal("checkpoint advanced", found, e)
	}
	r.InstrumentID = 2
	if _, _, e := PublishFinancialPackage(ctx, db, 3, sha, []domain.ProviderFinancialRecord{r}, inputs, "package", "md5"); e != nil {
		t.Fatal(e)
	}
	if value, found, e := GetCheckpoint(ctx, db, "tdx", "professional_financial", "package"); e != nil || !found || value != "md5" {
		t.Fatal(value, found, e)
	}
}
