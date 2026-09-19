package ingest

import (
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	"github.com/yinhm/alphalake/internal/source/cninfo"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestRealCNINFOPartialOverlapCannotComplete(t *testing.T) {
	const dir = "testdata/cninfo-partial-overlap-2026"
	ctx := t.Context()
	dbPath := filepath.Join(t.TempDir(), "overlap.duckdb")
	db, err := duckstore.OpenInitialized(ctx, dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	var metadata []struct {
		Page   int
		SHA256 string
	}
	if err := json.Unmarshal(readFinancialSample(t, dir, "pages.json"), &metadata); err != nil {
		t.Fatal(err)
	}
	source := &fakeCNINFOFilingSource{pages: map[int]cninfo.CataloguePage{}, raw: map[int][]byte{}}
	for _, m := range metadata {
		r, err := gzip.NewReader(bytes.NewReader(readFinancialSample(t, dir, fmt.Sprintf("page-%d.json.gz", m.Page))))
		if err != nil {
			t.Fatal(err)
		}
		raw, err := io.ReadAll(r)
		r.Close()
		if err != nil {
			t.Fatal(err)
		}
		if fmt.Sprintf("%x", sha256.Sum256(raw)) != m.SHA256 {
			t.Fatal("raw response hash", m.Page)
		}
		page, err := cninfo.ParseCataloguePage(raw)
		if err != nil {
			t.Fatal(err)
		}
		// 原始前六页，截为受控尾页：旧逻辑每页都有新增就会错误完成。
		page.Page, page.PageSize, page.TotalPages, page.TotalRecords = m.Page, 30, 6, 180
		source.pages[m.Page], source.raw[m.Page] = page, raw
	}
	if len(metadata) != 6 {
		t.Fatal("six original pages required")
	}
	day := time.Date(2025, 4, 29, 0, 0, 0, 0, time.UTC)
	key := "catalogue-window:v4:metadata-only=true:" + filingWindowName(day, day)
	if err := duckstore.SetCheckpoint(ctx, db, cninfo.Source, cninfoFilingDataset, key, "old-partial-pages"); err != nil {
		t.Fatal(err)
	}
	result, err := SyncCNINFOFilingsWithOptions(ctx, db, source, filepath.Join(t.TempDir(), "raw"), CNINFOFilingOptions{StartDate: day, EndDate: day, MetadataOnly: true, Now: func() time.Time { return time.Date(2027, 1, 1, 0, 0, 0, 0, time.UTC) }})
	if err == nil || source.catalogueCalls != 6 || result.SkippedWindows != 0 || len(result.Failures) != 1 || !errors.Is(result.Failures[0].Err, errCNINFOIncompletePages) {
		t.Fatalf("partial overlap accepted: %+v %v", result, err)
	}
	if !strings.Contains(result.Failures[0].Err.Error(), "3 repeated announcement identities") {
		t.Fatal(result.Failures[0].Err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	db, err = duckstore.Open(ctx, dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if _, found, err := duckstore.GetCheckpoint(ctx, db, cninfo.Source, cninfoFilingDataset, strings.Replace(key, ":v4:", ":v5:", 1)); err != nil || found {
		t.Fatalf("false completion after reopen: %t %v", found, err)
	}
	// 数量缺口也不能因上游提前宣称末页而得到完成键。
	source.pages[1] = cninfo.CataloguePage{Page: 1, TotalPages: 1, TotalRecords: 31, Filings: source.pages[1].Filings}
	result, err = SyncCNINFOFilingsWithOptions(ctx, db, source, filepath.Join(t.TempDir(), "raw"), CNINFOFilingOptions{StartDate: day, EndDate: day, MetadataOnly: true})
	if err == nil || len(result.Failures) != 1 || !strings.Contains(result.Failures[0].Err.Error(), "received 30 rows, source advertised 31") {
		t.Fatalf("short tail accepted: %+v %v", result, err)
	}
}

type fakeCNINFOFilingSource struct {
	foreignStockCode string
	requests         []cninfo.CatalogueRequest
	pages            map[int]cninfo.CataloguePage
	raw              map[int][]byte
	documents        map[string][]byte
	catalogueCalls   int
	documentCalls    int
}

func (f *fakeCNINFOFilingSource) CataloguePage(_ context.Context, request cninfo.CatalogueRequest) (cninfo.CataloguePage, []byte, error) {
	f.requests = append(f.requests, request)
	f.catalogueCalls++
	page, ok := f.pages[request.Page]
	if !ok {
		return cninfo.CataloguePage{}, nil, fmt.Errorf("unexpected page %d", request.Page)
	}
	if request.OrganizationID != "" && f.foreignStockCode != "" {
		page.Filings = append([]domain.FilingObservation(nil), page.Filings...)
		page.Filings[0].ProviderCode = f.foreignStockCode
	}
	return page, append([]byte(nil), f.raw[request.Page]...), nil
}

func TestCNINFOCodeCheckpointsAndForeignResponse(t *testing.T) {
	ctx := t.Context()
	db, err := duckstore.OpenInitialized(ctx, filepath.Join(t.TempDir(), "scope.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	day := time.Date(2025, 4, 29, 0, 0, 0, 0, time.UTC)
	period := time.Date(2025, 3, 31, 0, 0, 0, 0, time.UTC)
	source := &fakeCNINFOFilingSource{pages: map[int]cninfo.CataloguePage{}, raw: map[int][]byte{1: []byte(`{"fixture":"scope-checkpoint"}`)}}
	options := CNINFOFilingOptions{StartDate: day, EndDate: day, MetadataOnly: true, Now: func() time.Time { return time.Date(2027, 1, 1, 0, 0, 0, 0, time.UTC) }}
	root := filepath.Join(t.TempDir(), "raw")
	for _, code := range []string{"000001", "000002"} {
		f := domain.FilingObservation{Source: cninfo.Source, SourceFilingID: "test-" + code, ProviderCode: code, ProviderOrgID: "org" + code, Title: "2025年第一季度报告", FilingType: domain.FilingTypeQ1, FilingVariant: domain.FilingVariantFull, ReportPeriod: &period, AnnouncementTime: day, ClassifierVersion: cninfo.FilingClassifierVersion}
		source.pages[1] = cninfo.CataloguePage{Page: 1, TotalPages: 1, TotalRecords: 1, Filings: []domain.FilingObservation{f}}
		options.Code = code
		result, err := SyncCNINFOFilingsWithOptions(ctx, db, source, root, options)
		if err != nil || result.SkippedWindows != 0 || source.requests[len(source.requests)-1].Code != code || source.requests[len(source.requests)-1].OrganizationID != "org"+code {
			t.Fatalf("scope failed: %+v %v", result, err)
		}
	}
	key := "catalogue-window:v5:metadata-only=true:" + filingWindowName(day, day)
	if _, found, err := duckstore.GetCheckpoint(ctx, db, cninfo.Source, cninfoFilingDataset, key); err != nil || found {
		t.Fatalf("company query completed whole market: %t %v", found, err)
	}
	options.Code = "000001"
	result, err := SyncCNINFOFilingsWithOptions(ctx, db, source, root, options)
	if err != nil || result.SkippedWindows != 1 || source.catalogueCalls != 4 {
		t.Fatalf("scoped replay: %+v %v", result, err)
	}
	options.Code = "000003"
	source.pages[1].Filings[0].ProviderCode = "000003"
	source.pages[1].Filings[0].ProviderOrgID = "org000003"
	source.foreignStockCode = "000002"
	result, err = SyncCNINFOFilingsWithOptions(ctx, db, source, root, options)
	if err == nil || len(result.Failures) != 1 || !strings.Contains(result.Failures[0].Err.Error(), "returned another security") {
		t.Fatalf("foreign security accepted: %+v %v", result, err)
	}
	if _, found, err := duckstore.GetCheckpoint(ctx, db, cninfo.Source, cninfoFilingDataset, key+":code=000003:org=org000003"); err != nil || found {
		t.Fatalf("foreign response completed: %t %v", found, err)
	}
	// 同一代码存在两份有原始归档的机构身份，不能任意择一或继续触网。
	var artifactID int64
	if err := db.QueryRowContext(ctx, `SELECT catalogue_artifact_id FROM fundamental.filing WHERE provider_code='000001' LIMIT 1`).Scan(&artifactID); err != nil {
		t.Fatal(err)
	}
	_, err = duckstore.UpsertFilings(ctx, db, 1, []domain.FilingObservation{{Source: cninfo.Source, SourceFilingID: "other-org", ProviderCode: "000001", ProviderOrgID: "different-org", ClassifierVersion: cninfo.FilingClassifierVersion, AnnouncementTime: day, CatalogueArtifactID: artifactID}})
	if err != nil {
		t.Fatal(err)
	}
	calls := source.catalogueCalls
	options.Code = "000001"
	_, err = SyncCNINFOFilingsWithOptions(ctx, db, source, root, options)
	if err == nil || !strings.Contains(err.Error(), "multiple archived") || source.catalogueCalls != calls {
		t.Fatalf("ambiguous organization accepted: %v", err)
	}
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
	db, err := duckstore.OpenInitialized(ctx, filepath.Join(t.TempDir(), "cninfo.duckdb"))
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
		pages:     map[int]cninfo.CataloguePage{1: {Page: 1, PageSize: 50, TotalPages: 1, TotalRecords: 1, Filings: []domain.FilingObservation{filing}}},
		raw:       map[int][]byte{1: []byte(`{"announcements":[{"announcementId":"1212345678"}]}`)},
		documents: map[string][]byte{"finalpage/report.pdf": []byte("%PDF-authoritative")},
	}
	options := CNINFOFilingOptions{
		StartDate: time.Date(2026, 3, 1, 0, 0, 0, 0, time.UTC),
		EndDate:   time.Date(2026, 3, 31, 0, 0, 0, 0, time.UTC),
		Now:       func() time.Time { return time.Date(2026, 4, 1, 0, 0, 0, 0, time.UTC) },
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
	db, err := duckstore.OpenInitialized(ctx, filepath.Join(t.TempDir(), "cninfo-pending.duckdb"))
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
		pages:     map[int]cninfo.CataloguePage{1: {Page: 1, PageSize: 50, TotalPages: 1, Filings: []domain.FilingObservation{filing}}},
		raw:       map[int][]byte{1: []byte(`{"announcements":[]}`)},
		documents: map[string][]byte{"historical.pdf": []byte("%PDF-old")},
	}
	summary, err := SyncCNINFOFilingsWithOptions(ctx, db, source, root, CNINFOFilingOptions{
		StartDate: time.Date(2001, 3, 1, 0, 0, 0, 0, time.UTC),
		EndDate:   time.Date(2001, 3, 1, 0, 0, 0, 0, time.UTC),
		Now:       func() time.Time { return time.Date(2001, 3, 2, 0, 0, 0, 0, time.UTC) },
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

func TestSyncCNINFOFilingsRejectsHTMLDocumentAndWithholdsCheckpoint(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenInitialized(ctx, filepath.Join(t.TempDir(), "cninfo-html.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	root := filepath.Join(t.TempDir(), "raw")
	if _, err := duckstore.UpsertInstrument(ctx, db,
		domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHG", Currency: "CNY", Name: "Test"},
		domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600001"},
	); err != nil {
		t.Fatal(err)
	}
	period := time.Date(2025, 12, 31, 0, 0, 0, 0, time.UTC)
	filing := domain.FilingObservation{
		Source: cninfo.Source, SourceFilingID: "anti-bot", ProviderCode: "600001", ExchangeMIC: "XSHG",
		Title: "2025年年度报告", FilingType: domain.FilingTypeAnnual, FilingVariant: domain.FilingVariantFull,
		ReportPeriod: &period, AnnouncementTime: time.Date(2026, 3, 28, 10, 0, 0, 0, time.UTC),
		DocumentLocator: "anti-bot.pdf", ClassifierVersion: cninfo.FilingClassifierVersion,
	}
	source := &fakeCNINFOFilingSource{
		pages:     map[int]cninfo.CataloguePage{1: {Page: 1, PageSize: 50, TotalPages: 1, Filings: []domain.FilingObservation{filing}}},
		raw:       map[int][]byte{1: []byte(`{"announcements":[]}`)},
		documents: map[string][]byte{"anti-bot.pdf": []byte("<!doctype html><html>challenge</html>")},
	}
	summary, err := SyncCNINFOFilingsWithOptions(ctx, db, source, root, CNINFOFilingOptions{
		StartDate: time.Date(2026, 3, 28, 0, 0, 0, 0, time.UTC),
		EndDate:   time.Date(2026, 3, 28, 0, 0, 0, 0, time.UTC),
		Now:       func() time.Time { return time.Date(2026, 3, 29, 0, 0, 0, 0, time.UTC) },
	})
	if err == nil || len(summary.Failures) != 1 {
		t.Fatalf("summary=%#v err=%v", summary, err)
	}
	var documents, checkpoints int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM meta.artifact WHERE source='cninfo' AND dataset='filing_document'`).Scan(&documents); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM meta.checkpoint WHERE source='cninfo' AND dataset='filing'`).Scan(&checkpoints); err != nil {
		t.Fatal(err)
	}
	if documents != 0 || checkpoints != 0 {
		t.Fatalf("documents/checkpoints=%d/%d, want 0/0", documents, checkpoints)
	}
}

func TestValidateCNINFOFilingDocument(t *testing.T) {
	for _, tc := range []struct {
		name      string
		url       string
		mediaType string
		content   []byte
		wantError bool
	}{
		{name: "pdf", url: "https://static.cninfo.test/a.pdf", mediaType: "application/pdf", content: []byte("%PDF-ok")},
		{name: "empty", url: "https://static.cninfo.test/a.pdf", content: nil, wantError: true},
		{name: "html media", url: "https://static.cninfo.test/a.pdf", mediaType: "text/html", content: []byte("challenge"), wantError: true},
		{name: "html bytes", url: "https://static.cninfo.test/a.pdf", mediaType: "application/octet-stream", content: []byte("<html>challenge</html>"), wantError: true},
		{name: "bad pdf", url: "https://static.cninfo.test/a.pdf", mediaType: "application/pdf", content: []byte("not-pdf"), wantError: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			err := validateCNINFOFilingDocument(tc.url, tc.mediaType, tc.content)
			if (err != nil) != tc.wantError {
				t.Fatalf("error=%v wantError=%v", err, tc.wantError)
			}
		})
	}
}

func TestCNINFOCheckpointRequiresRequestedEvidence(t *testing.T) {
	ctx := t.Context()
	db, err := duckstore.OpenInitialized(ctx, filepath.Join(t.TempDir(), "checkpoint.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	day := time.Date(2026, 3, 28, 0, 0, 0, 0, time.UTC)
	period := time.Date(2025, 12, 31, 0, 0, 0, 0, time.UTC)
	source := &fakeCNINFOFilingSource{
		pages: map[int]cninfo.CataloguePage{1: {Page: 1, TotalPages: 1, Filings: []domain.FilingObservation{{
			Source: cninfo.Source, SourceFilingID: "original", ProviderCode: "000001",
			FilingType: domain.FilingTypeAnnual, FilingVariant: domain.FilingVariantFull,
			ReportPeriod: &period, AnnouncementTime: day,
			ClassifierVersion: "test", DocumentLocator: "report.pdf",
		}}}},
		raw:       map[int][]byte{1: []byte("{}")},
		documents: map[string][]byte{"report.pdf": []byte("%PDF-test")},
	}
	root := filepath.Join(t.TempDir(), "raw")
	options := CNINFOFilingOptions{StartDate: day, EndDate: day, MetadataOnly: true,
		Now: func() time.Time { return day.AddDate(2, 0, 0) }}
	// An old checkpoint cannot prove document completeness.
	if err := duckstore.SetCheckpoint(ctx, db, cninfo.Source, cninfoFilingDataset, "catalogue-window:"+filingWindowName(day, day), "legacy"); err != nil {
		t.Fatal(err)
	}
	if _, err := SyncCNINFOFilingsWithOptions(ctx, db, source, root, options); err != nil {
		t.Fatal(err)
	}
	if source.documentCalls != 0 {
		t.Fatal("metadata-only sync downloaded a document")
	}
	options.MetadataOnly = false
	if err := duckstore.SetCheckpoint(ctx, db, cninfo.Source, cninfoFilingDataset, "catalogue-window:v2:metadata-only=false:"+filingWindowName(day, day), "truncated-pages"); err != nil {
		t.Fatal(err)
	}
	full, err := SyncCNINFOFilingsWithOptions(ctx, db, source, root, options)
	if err != nil || full.Documents != 1 || full.SkippedWindows != 0 {
		t.Fatalf("full sync=%#v, err=%v", full, err)
	}
	replay, err := SyncCNINFOFilingsWithOptions(ctx, db, source, root, options)
	if err != nil || replay.SkippedWindows != 1 || source.documentCalls != 1 {
		t.Fatalf("replay=%#v, err=%v, downloads=%d", replay, err, source.documentCalls)
	}
}

func TestCNINFOPaginationRejectsContradictoryPages(t *testing.T) {
	ctx := t.Context()
	db, err := duckstore.OpenInitialized(ctx, filepath.Join(t.TempDir(), "pagination.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	day := time.Date(2026, 3, 28, 0, 0, 0, 0, time.UTC)
	source := &fakeCNINFOFilingSource{
		pages: map[int]cninfo.CataloguePage{},
		raw:   map[int][]byte{1: []byte("{}")},
	}
	root := filepath.Join(t.TempDir(), "raw")
	for _, page := range []cninfo.CataloguePage{
		{Page: 2, TotalPages: 1},
		{Page: 1, TotalPages: 1, TotalRecords: 1},
	} {
		source.pages[1] = page
		_, _, failures, _ := acquireCNINFOFilingWindow(ctx, db, source, root, 1, day, day, day, 50, &CNINFOFilingSummary{}, CNINFOFilingOptions{})
		if len(failures) == 0 {
			t.Fatalf("accepted inconsistent page: %#v", page)
		}
	}
}

func TestRealCNINFORepeatedPagesInvalidateOldCompletion(t *testing.T) {
	ctx := t.Context()
	path := filepath.Join(t.TempDir(), "repeat.duckdb")
	db, err := duckstore.OpenInitialized(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	source := &fakeCNINFOFilingSource{pages: map[int]cninfo.CataloguePage{}, raw: map[int][]byte{}}
	for i := 1; i <= 2; i++ {
		compressed, e := os.ReadFile(fmt.Sprintf("testdata/cninfo-repeated-pages-2026/page-%d.json.gz", i))
		if e != nil {
			t.Fatal(e)
		}
		reader, e := gzip.NewReader(bytes.NewReader(compressed))
		if e != nil {
			t.Fatal(e)
		}
		raw, e := io.ReadAll(reader)
		reader.Close()
		if e != nil {
			t.Fatal(e)
		}
		page, e := cninfo.ParseCataloguePage(raw)
		if e != nil {
			t.Fatal(e)
		}
		page.Page = i
		page.PageSize = 30
		source.pages[i] = page
		source.raw[i] = raw
	}
	_, _, defaultSize, _, e := normalizeCNINFOFilingOptions(CNINFOFilingOptions{}, time.Now())
	if e != nil || defaultSize != 30 {
		t.Fatalf("unsupported live default size %d %v", defaultSize, e)
	}
	compressed, e := os.ReadFile("testdata/cninfo-repeated-pages-2026/size-30-page-2.json.gz")
	if e != nil {
		t.Fatal(e)
	}
	reader, e := gzip.NewReader(bytes.NewReader(compressed))
	if e != nil {
		t.Fatal(e)
	}
	raw, e := io.ReadAll(reader)
	reader.Close()
	if e != nil {
		t.Fatal(e)
	}
	good, e := cninfo.ParseCataloguePage(raw)
	if e != nil {
		t.Fatal(e)
	}
	firstIDs := map[string]bool{}
	for _, f := range source.pages[1].Filings {
		firstIDs[f.SourceFilingID] = true
	}
	if len(good.Filings) != 30 {
		t.Fatal("expected 30 original announcements")
	}
	for _, f := range good.Filings {
		if firstIDs[f.SourceFilingID] {
			t.Fatal("size-30 page 2 did not advance")
		}
	}
	start := time.Date(2025, 6, 28, 0, 0, 0, 0, time.UTC)
	end := time.Date(2025, 6, 28, 0, 0, 0, 0, time.UTC)
	oldKey := "catalogue-window:v3:metadata-only=true:" + filingWindowName(start, end)
	if err = duckstore.SetCheckpoint(ctx, db, cninfo.Source, cninfoFilingDataset, oldKey, "incomplete-old-run"); err != nil {
		t.Fatal(err)
	}
	result, err := SyncCNINFOFilingsWithOptions(ctx, db, source, filepath.Join(t.TempDir(), "raw"), CNINFOFilingOptions{StartDate: start, EndDate: end, MetadataOnly: true, Now: func() time.Time { return time.Date(2026, 9, 9, 0, 0, 0, 0, time.UTC) }})
	if err == nil || source.catalogueCalls != 2 || result.SkippedWindows != 0 || len(result.Failures) != 1 || !strings.Contains(result.Failures[0].Err.Error(), "no progress") {
		t.Fatalf("result=%+v calls=%d err=%v", result, source.catalogueCalls, err)
	}
	if err = db.Close(); err != nil {
		t.Fatal(err)
	}
	db, err = duckstore.Open(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if _, found, e := duckstore.GetCheckpoint(ctx, db, cninfo.Source, cninfoFilingDataset, strings.Replace(oldKey, ":v3:", ":v5:", 1)); e != nil || found {
		t.Fatalf("false completion: %t %v", found, e)
	}
	var n int
	if err = db.QueryRowContext(ctx, `SELECT count(*) FROM meta.artifact WHERE source='cninfo'`).Scan(&n); err != nil || n != 2 {
		t.Fatalf("raw evidence %d %v", n, err)
	}
	var status string
	if err = db.QueryRowContext(ctx, `SELECT status FROM meta.ingest_run WHERE ingest_run_id=?`, result.RunID).Scan(&status); err != nil || status == "completed" {
		t.Fatalf("run=%s %v", status, err)
	}
}

type splittingCNINFOFilingSource struct{ fakeCNINFOFilingSource }

func (f *splittingCNINFOFilingSource) CataloguePage(_ context.Context, request cninfo.CatalogueRequest) (cninfo.CataloguePage, []byte, error) {
	f.catalogueCalls++
	record := f.pages[1].Filings[0]
	page := cninfo.CataloguePage{Page: request.Page, PageSize: 30, TotalPages: 2, TotalRecords: 60, HasMore: request.Page == 1, Filings: []domain.FilingObservation{record}}
	if request.StartDate.Equal(request.EndDate) {
		record.SourceFilingID = request.StartDate.Format("20060102")
		record.AnnouncementDate = request.StartDate
		record.AnnouncementTime = request.StartDate.Add(24 * time.Hour)
		record.RawAnnouncementTimeMillis = request.StartDate.UnixMilli()
		page.Filings = []domain.FilingObservation{record}
		page.TotalPages = 1
		page.TotalRecords = 1
		page.HasMore = false
	}
	return page, []byte(fmt.Sprintf(`{"test_request":"%s/%s/%d"}`, request.StartDate.Format("2006-01-02"), request.EndDate.Format("2006-01-02"), request.Page)), nil
}

func TestCNINFOAutomaticallySplitsStalledWindow(t *testing.T) {
	ctx := t.Context()
	path := filepath.Join(t.TempDir(), "split.duckdb")
	db, err := duckstore.OpenInitialized(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	compressed, err := os.ReadFile("testdata/cninfo-repeated-pages-2026/page-1.json.gz")
	if err != nil {
		t.Fatal(err)
	}
	reader, err := gzip.NewReader(bytes.NewReader(compressed))
	if err != nil {
		t.Fatal(err)
	}
	raw, err := io.ReadAll(reader)
	reader.Close()
	if err != nil {
		t.Fatal(err)
	}
	page, err := cninfo.ParseCataloguePage(raw)
	if err != nil {
		t.Fatal(err)
	}
	source := &splittingCNINFOFilingSource{fakeCNINFOFilingSource{pages: map[int]cninfo.CataloguePage{1: page}}}
	start := time.Date(2025, 6, 27, 0, 0, 0, 0, time.UTC)
	end := start.AddDate(0, 0, 1)
	options := CNINFOFilingOptions{StartDate: start, EndDate: end, MetadataOnly: true, Now: func() time.Time { return time.Date(2026, 9, 9, 0, 0, 0, 0, time.UTC) }}
	result, err := SyncCNINFOFilingsWithOptions(ctx, db, source, filepath.Join(t.TempDir(), "raw"), options)
	if err != nil || len(result.Failures) != 0 || result.Windows != 3 || result.Filings != 2 || source.catalogueCalls != 4 {
		t.Fatalf("%+v calls=%d err=%v", result, source.catalogueCalls, err)
	}
	for _, day := range []time.Time{start, end} {
		if _, found, e := duckstore.GetCheckpoint(ctx, db, cninfo.Source, cninfoFilingDataset, "catalogue-window:v5:metadata-only=true:"+filingWindowName(day, day)); e != nil || !found {
			t.Fatalf("missing child completion %v %v", day, e)
		}
	}
	if _, found, e := duckstore.GetCheckpoint(ctx, db, cninfo.Source, cninfoFilingDataset, "catalogue-window:v5:metadata-only=true:"+filingWindowName(start, end)); e != nil || found {
		t.Fatalf("unexpected parent completion %v %v", found, e)
	}
	var n int
	if err = db.QueryRowContext(ctx, `SELECT count(*) FROM meta.validation_result WHERE rule_code='cninfo.catalogue_window_split'`).Scan(&n); err != nil || n != 1 {
		t.Fatalf("diagnostic %d %v", n, err)
	}
	result, err = SyncCNINFOFilingsWithOptions(ctx, db, source, filepath.Join(t.TempDir(), "raw"), options)
	if err != nil || result.SkippedWindows != 2 || result.Inserted != 0 {
		t.Fatalf("replay %+v %v", result, err)
	}
}
