package duckdb

import (
	"context"
	"database/sql"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

func TestRefreshProviderFilingLinksUsesObservationTimeAndCorrections(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "provider-filing.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	instrumentID, err := UpsertInstrument(ctx, db,
		domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHE", Currency: "CNY", Name: "Test"},
		domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sz000001"},
	)
	if err != nil {
		t.Fatal(err)
	}
	period := time.Date(2025, 12, 31, 0, 0, 0, 0, time.UTC)
	originalTime := time.Date(2026, 3, 28, 10, 0, 0, 0, time.UTC)
	correctionTime := time.Date(2026, 5, 10, 9, 0, 0, 0, time.UTC)
	filings := []domain.FilingObservation{
		{
			InstrumentID: instrumentID, Source: "cninfo", SourceFilingID: "original", ProviderCode: "000001",
			Title: "2025年年度报告", FilingType: domain.FilingTypeAnnual, FilingVariant: domain.FilingVariantFull,
			ReportPeriod: &period, AnnouncementTime: originalTime, ClassifierVersion: "test", ResolutionStatus: domain.FilingResolutionResolved,
		},
		{
			InstrumentID: instrumentID, Source: "cninfo", SourceFilingID: "corrected", ProviderCode: "000001",
			Title: "2025年年度报告（更正后）", FilingType: domain.FilingTypeAnnual, FilingVariant: domain.FilingVariantCorrectedReport,
			ReportPeriod: &period, AnnouncementTime: correctionTime, ClassifierVersion: "test", ResolutionStatus: domain.FilingResolutionResolved, IsCorrection: true,
		},
	}
	if _, err := UpsertFilings(ctx, db, 1, filings); err != nil {
		t.Fatal(err)
	}
	artifactA := insertTestArtifact(t, ctx, db, "rev-a", time.Date(2026, 4, 1, 0, 0, 0, 0, time.UTC))
	artifactB := insertTestArtifact(t, ctx, db, "rev-b", time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC))
	insertTestProviderRecord(t, ctx, db, artifactA, instrumentID, "rev-a", "000001", period)
	insertTestProviderRecord(t, ctx, db, artifactB, instrumentID, "rev-b", "000001", period)

	result, err := RefreshProviderFilingLinks(ctx, db, 7, "tdx")
	if err != nil {
		t.Fatal(err)
	}
	if result.Records != 2 || result.Linked != 2 || result.Pending != 0 || result.Ambiguous != 0 {
		t.Fatalf("result=%#v", result)
	}
	var linkedOriginal, linkedCorrection string
	if err := db.QueryRowContext(ctx, `
		SELECT f.source_filing_id
		FROM fundamental.provider_filing_link l
		JOIN fundamental.filing f ON f.filing_id=l.filing_id
		WHERE l.provider_revision_key='rev-a'
	`).Scan(&linkedOriginal); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRowContext(ctx, `
		SELECT f.source_filing_id
		FROM fundamental.provider_filing_link l
		JOIN fundamental.filing f ON f.filing_id=l.filing_id
		WHERE l.provider_revision_key='rev-b'
	`).Scan(&linkedCorrection); err != nil {
		t.Fatal(err)
	}
	if linkedOriginal != "original" || linkedCorrection != "corrected" {
		t.Fatalf("links A/B=%s/%s", linkedOriginal, linkedCorrection)
	}
}

func TestRefreshProviderFilingLinksKeepsFutureAndTiedCandidatesUnlinked(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "provider-filing-ambiguous.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	instrumentID, err := UpsertInstrument(ctx, db,
		domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHG", Currency: "CNY", Name: "Test"},
		domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600001"},
	)
	if err != nil {
		t.Fatal(err)
	}
	period := time.Date(2025, 12, 31, 0, 0, 0, 0, time.UTC)
	observed := time.Date(2026, 3, 1, 0, 0, 0, 0, time.UTC)
	artifactPending := insertTestArtifact(t, ctx, db, "future-rev", observed)
	insertTestProviderRecord(t, ctx, db, artifactPending, instrumentID, "future-rev", "600001", period)
	future := domain.FilingObservation{
		InstrumentID: instrumentID, Source: "cninfo", SourceFilingID: "future", ProviderCode: "600001",
		Title: "2025年年度报告", FilingType: domain.FilingTypeAnnual, FilingVariant: domain.FilingVariantFull,
		ReportPeriod: &period, AnnouncementTime: observed.AddDate(0, 0, 20), ClassifierVersion: "test", ResolutionStatus: domain.FilingResolutionResolved,
	}
	if _, err := UpsertFilings(ctx, db, 1, []domain.FilingObservation{future}); err != nil {
		t.Fatal(err)
	}
	result, err := RefreshProviderFilingLinks(ctx, db, 7, "tdx")
	if err != nil {
		t.Fatal(err)
	}
	if result.Pending != 1 || result.Linked != 0 {
		t.Fatalf("future result=%#v", result)
	}

	// Add two equally-ranked full reports before a second provider revision. The
	// linker refuses to choose one merely by provider ID ordering.
	tieTime := time.Date(2026, 4, 1, 10, 0, 0, 0, time.UTC)
	for _, id := range []string{"tie-a", "tie-b"} {
		filing := future
		filing.SourceFilingID = id
		filing.AnnouncementTime = tieTime
		if _, err := UpsertFilings(ctx, db, 2, []domain.FilingObservation{filing}); err != nil {
			t.Fatal(err)
		}
	}
	artifactAmbiguous := insertTestArtifact(t, ctx, db, "tie-rev", time.Date(2026, 4, 2, 0, 0, 0, 0, time.UTC))
	insertTestProviderRecord(t, ctx, db, artifactAmbiguous, instrumentID, "tie-rev", "600001", period)
	result, err = RefreshProviderFilingLinks(ctx, db, 8, "tdx")
	if err != nil {
		t.Fatal(err)
	}
	if result.Ambiguous != 1 || result.Pending != 1 || result.Linked != 0 {
		t.Fatalf("tie result=%#v", result)
	}
	var status string
	var filingID *int64
	if err := db.QueryRowContext(ctx, `SELECT status, filing_id FROM fundamental.provider_filing_link WHERE provider_revision_key='tie-rev'`).Scan(&status, &filingID); err != nil {
		t.Fatal(err)
	}
	if status != ProviderFilingAmbiguous || filingID != nil {
		t.Fatalf("tie link status/id=%s/%v", status, filingID)
	}
}

func insertTestArtifact(t *testing.T, ctx context.Context, db interface {
	QueryRowContext(context.Context, string, ...any) *sql.Row
}, sha string, fetchedAt time.Time) int64 {
	t.Helper()
	var artifactID int64
	if err := db.QueryRowContext(ctx, `
		INSERT INTO meta.artifact (
			source, dataset, source_locator, fetched_at, sha256, content_length
		) VALUES ('tdx','professional_financial',?, ?, ?, 1)
		RETURNING artifact_id
	`, "tdxfin/"+sha+".zip", fetchedAt, sha).Scan(&artifactID); err != nil {
		t.Fatal(err)
	}
	return artifactID
}

func insertTestProviderRecord(t *testing.T, ctx context.Context, db interface {
	ExecContext(context.Context, string, ...any) (sql.Result, error)
}, artifactID, instrumentID int64, revision, code string, period time.Time) {
	t.Helper()
	if _, err := db.ExecContext(ctx, `
		INSERT INTO fundamental.provider_fact (
			instrument_id, source, report_period, provider_code, provider_field,
			value, artifact_id, revision_key
		) VALUES (?, 'tdx', ?, ?, 'FN230', 100, ?, ?)
	`, instrumentID, period, code, artifactID, revision); err != nil {
		t.Fatal(err)
	}
}
