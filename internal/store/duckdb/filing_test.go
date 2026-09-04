package duckdb

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

func TestResolveAndUpsertFilings(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "filing.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	if _, err := UpsertInstrument(ctx, db,
		domain.InstrumentRef{Type: domain.InstrumentIndex, ExchangeMIC: "XSHG", Currency: "CNY", Name: "上证指数"},
		domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh000001"},
	); err != nil {
		t.Fatal(err)
	}
	bankID, err := UpsertInstrument(ctx, db,
		domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHE", Currency: "CNY", Name: "平安银行"},
		domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sz000001"},
	)
	if err != nil {
		t.Fatal(err)
	}
	period := time.Date(2025, 12, 31, 0, 0, 0, 0, time.UTC)
	announced := time.Date(2026, 3, 28, 10, 42, 0, 0, time.UTC)
	filings, err := ResolveFilingObservations(ctx, db, []domain.FilingObservation{
		{
			Source: "cninfo", SourceFilingID: "annual-a", ProviderCode: "000001", ExchangeMIC: "XSHE",
			SecurityName: "平安银行", Title: "2025年年度报告", FilingType: domain.FilingTypeAnnual,
			FilingVariant: domain.FilingVariantFull, ReportPeriod: &period, AnnouncementTime: announced,
			ClassifierVersion: "test-v1", CatalogueArtifactID: 11,
			DocumentArtifactID: 21, DocumentSHA256: "sha-a", SourceURL: "https://static.cninfo.com.cn/a.pdf",
		},
		{
			Source: "cninfo", SourceFilingID: "unknown", ProviderCode: "999999", ExchangeMIC: "XSHE",
			Title: "2025年年度报告", FilingType: domain.FilingTypeAnnual,
			FilingVariant: domain.FilingVariantFull, ReportPeriod: &period, AnnouncementTime: announced,
			ClassifierVersion: "test-v1",
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	if filings[0].InstrumentID != bankID || filings[0].ResolutionStatus != domain.FilingResolutionResolved {
		t.Fatalf("resolved filing=%#v", filings[0])
	}
	if filings[1].InstrumentID != 0 || filings[1].ResolutionStatus != domain.FilingResolutionPending || filings[1].ResolutionReason == "" {
		t.Fatalf("pending filing=%#v", filings[1])
	}
	result, err := UpsertFilings(ctx, db, 1, filings)
	if err != nil {
		t.Fatal(err)
	}
	if result.Attempted != 2 || result.Inserted != 2 || result.Resolved != 1 || result.Pending != 1 || result.Documents != 1 {
		t.Fatalf("result=%#v", result)
	}
	result, err = UpsertFilings(ctx, db, 2, filings)
	if err != nil {
		t.Fatal(err)
	}
	if result.Inserted != 0 || result.Updated != 2 {
		t.Fatalf("replay result=%#v", result)
	}

	var storedInstrument int64
	var status, reason string
	if err := db.QueryRowContext(ctx, `
		SELECT instrument_id, resolution_status, COALESCE(resolution_reason,'')
		FROM fundamental.filing WHERE source_filing_id='annual-a'
	`).Scan(&storedInstrument, &status, &reason); err != nil {
		t.Fatal(err)
	}
	if storedInstrument != bankID || status != domain.FilingResolutionResolved || reason != "" {
		t.Fatalf("stored=%d/%s/%q", storedInstrument, status, reason)
	}
	var documents int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.filing_document`).Scan(&documents); err != nil {
		t.Fatal(err)
	}
	if documents != 1 {
		t.Fatalf("documents=%d, want 1 immutable revision", documents)
	}
}

func TestCorrectionFilingLinksImmediatePriorAnchor(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "correction.duckdb"))
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
	original := domain.FilingObservation{
		InstrumentID: instrumentID, Source: "cninfo", SourceFilingID: "original", ProviderCode: "600001", ExchangeMIC: "XSHG",
		Title: "2025年年度报告", FilingType: domain.FilingTypeAnnual, FilingVariant: domain.FilingVariantFull,
		ReportPeriod: &period, AnnouncementTime: time.Date(2026, 3, 28, 10, 0, 0, 0, time.UTC),
		ClassifierVersion: "test-v1", ResolutionStatus: domain.FilingResolutionResolved,
	}
	correction := original
	correction.SourceFilingID = "correction"
	correction.Title = "2025年年度报告（更正后）"
	correction.FilingVariant = domain.FilingVariantCorrectedReport
	correction.IsCorrection = true
	correction.AnnouncementTime = time.Date(2026, 5, 10, 9, 0, 0, 0, time.UTC)
	if _, err := UpsertFilings(ctx, db, 1, []domain.FilingObservation{original, correction}); err != nil {
		t.Fatal(err)
	}
	var originalID, predecessor int64
	if err := db.QueryRowContext(ctx, `SELECT filing_id FROM fundamental.filing WHERE source_filing_id='original'`).Scan(&originalID); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRowContext(ctx, `SELECT corrects_filing_id FROM fundamental.filing WHERE source_filing_id='correction'`).Scan(&predecessor); err != nil {
		t.Fatal(err)
	}
	if predecessor != originalID {
		t.Fatalf("correction predecessor=%d, want %d", predecessor, originalID)
	}
}
