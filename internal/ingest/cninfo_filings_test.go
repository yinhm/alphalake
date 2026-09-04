package ingest

import (
	"context"
	"fmt"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	"github.com/yinhm/alphalake/internal/source/cninfo"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

type fakeCNINFOFilingSource struct {
	pages         map[int]cninfo.CataloguePage
	raw           map[int][]byte
	documents     map[string][]byte
	catalogueCalls int
	documentCalls int
}

func (f *fakeCNINFOFilingSource) CataloguePage(_ context.Context, request cninfo.CatalogueRequest) (cninfo.CataloguePage, []byte, error) {
	f.catalogueCalls++
	page, ok := f.pages[request.Page]
	if !ok {
		return cninfo.CataloguePage{}, nil, fmt.Errorf("unexpected page %d", request.Page)
	}
	return page, append([]byte(nil), f.raw[request.Page]...), nil
}

func (f *fakeCNINFOFilingSource) FilingDocumentURL(locator string) (string, error) {
	return "https://static.cninfo.test/" + locator, nil
}

func (f *fakeCNINFOFilingSource) FilingDocument(_ context.Context, locator string) ([]byte, string, string, error) {
	f.documentCalls++
	content, ok := f.documents[locator]
	if !ok {
		return nil, "", "", fmt.Errorf("missing document %s", locator)
	}
	return append([]byte(nil), content...), "https://static.cninfo.test/" + locator, "application/pdf", nil
}

func TestSyncCNINFOFilingsPersistsEvidenceAndReusesDocument(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "cninfo.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	root := filepath.Join(t.TempDir(), "raw")
	instrumentID, err := duckstore.UpsertInstrument(ctx, db,
		domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHE", Currency: "CNY", Name: "平安银行"},
		domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sz000001"},
	)
	if err != nil {
		t.Fatal(err)
	}
	period := time.Date(2025, 12, 31, 0, 0, 0, 0, time.UTC)
	announcement := time.Date(2026, 3, 28, 10, 42, 0, 0, time.UTC)
	filing := domain.FilingObservation{
		Source: cninfo.Source, SourceFilingID: "1212345678", ProviderCode: "000001", ExchangeMIC: "XSHE",
		SecurityName: "平安银行", Title: "2025年年度报告", FilingType: domain.FilingTypeAnnual,
		FilingVariant: domain.FilingVariantFull, ReportPeriod: &period, AnnouncementTime: announcement,
		DocumentLocator: "finalpage/report.pdf", ClassifierVersion: cninfo.FilingClassifierVersion,
		ProviderOrgID: "gssz0000001", ProviderColumnID: "szse", ProviderPageColumn: "sz",
		RawAnnouncementTimeMillis: announcement.UnixMilli(),
	}
	source := &fakeCNINFOFilingSource{
		pages: map[int]cninfo.CataloguePage{1: {Page: 1, PageSize: 50, TotalPages: 1, TotalRecords: 1, Filings: []domain.FilingObservation{filing}}},
		raw: map[int][]byte{1: []byte(`{"announcements":[{"announcementId":"1212345678"}]}`)},
		documents: map[string][]byte{"finalpage/report.pdf": []byte("%PDF-authoritative")},
	}
	options := CNINFOFilingOptions{
		StartDate: time.Date(2026, 3, 1, 0, 0, 0, 0, time.UTC),
		EndDate: time.Date(2026, 3, 31, 0, 0, 0, 0, time.UTC),
		Now: func() time.Time { return time.Date(2026, 4, 1, 0, 0, 0, 0, time.UTC) },
	}
	first, err := SyncCNINFOFilingsWithOptions(ctx, db, source, root, options)
	if err != nil {
		t.Fatal(err)
	}
	if first.Pages != 1 || first.Filings != 1 || first.Inserted != 1 || first.Resolved != 1 || first.Pending != 0 || first.Documents != 1 || source.documentCalls != 1 {
		t.Fatalf("first=%#v documentCalls=%d", first, source.documentCalls)
	}
	var storedInstrument, filingDocuments, artifacts, checkpoints int64
	if err := db.QueryRowContext(ctx, `SELECT instrument_id FROM fundamental.filing WHERE source_filing_id='1212345678'`).Scan(&storedInstrument); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.filing_document`).Scan(&filingDocuments); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM meta.artifact WHERE source='cninfo'`).Scan(&artifacts); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM meta.checkpoint WHERE source='cninfo' AND dataset='filing'`).Scan(&checkpoints); err != nil {
		t.Fatal(err)
	}
	if storedInstrument != instrumentID || filingDocuments != 1 || artifacts != 2 || checkpoints != 1 {
		t.Fatalf("stored instrument/docs/artifacts/checkpoints=%d/%d/%d/%d", storedInstrument, filingDocuments, artifacts, checkpoints)
	}

	second, err := SyncCNINFOFilingsWithOptions(ctx, db, source, root, options)
	if err != nil {
		t.Fatal(err)
	}
	if second.Updated != 1 || second.ReusedDocs != 1 || second.Documents != 0 || source.documentCalls != 1 {
		t.Fatalf("second=%#v documentCalls=%d", second, source.documentCalls)
	}
}

func TestSyncCNINFOFilingsKeepsUnresolvedEvidenceWithoutFailingWindow(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "cninfo-pending.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	root := filepath.Join(t.TempDir(), "raw")
	period := time.Date(2000, 12, 31, 0, 0, 0, 0, time.UTC)
	filing := domain.FilingObservation{
		Source: cninfo.Source, SourceFilingID: "historical", ProviderCode: "430001", ExchangeMIC: "XBSE",
		Title: "2000年年度报告", FilingType: domain.FilingTypeAnnual, FilingVariant: domain.FilingVariantFull,
		ReportPeriod: &period, AnnouncementTime: time.Date(2001, 3, 1, 0, 0, 0, 0, time.UTC),
		DocumentLocator: "historical.pdf", ClassifierVersion: cninfo.FilingClassifierVersion,
	}
	source := &fakeCNINFOFilingSource{
		pages: map[int]cninfo.CataloguePage{1: {Page: 1, PageSize: 50, TotalPages: 1, Filings: []domain.FilingObservation{filing}}},
		raw: map[int][]byte{1: []byte(`{"announcements":[]}`)},
		documents: map[string][]byte{"historical.pdf": []byte("%PDF-old")},
	}
	summary, err := SyncCNINFOFilingsWithOptions(ctx, db, source, root, CNINFOFilingOptions{
		StartDate: time.Date(2001, 3, 1, 0, 0, 0, 0, time.UTC),
		EndDate: time.Date(2001, 3, 1, 0, 0, 0, 0, time.UTC),
		Now: func() time.Time { return time.Date(2001, 3, 2, 0, 0, 0, 0, time.UTC) },
	})
	if err != nil {
		t.Fatal(err)
	}
	if summary.Pending != 1 || summary.Filings != 1 || summary.Documents != 1 {
		t.Fatalf("summary=%#v", summary)
	}
	var status, reason string
	if err := db.QueryRowContext(ctx, `SELECT resolution_status, COALESCE(resolution_reason,'') FROM fundamental.filing WHERE source_filing_id='historical'`).Scan(&status, &reason); err != nil {
		t.Fatal(err)
	}
	if status != domain.FilingResolutionPending || reason == "" {
		t.Fatalf("status/reason=%s/%q", status, reason)
	}
	var runStatus string
	if err := db.QueryRowContext(ctx, `SELECT status FROM meta.ingest_run WHERE ingest_run_id=?`, summary.RunID).Scan(&runStatus); err != nil {
		t.Fatal(err)
	}
	if runStatus != duckstore.IngestRunPartial {
		t.Fatalf("run status=%s, want partial", runStatus)
	}
}
