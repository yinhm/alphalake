package ingest

import (
	"context"
	"database/sql"
	"fmt"
	"math"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/artifact"
	"github.com/yinhm/alphalake/internal/domain"
	"github.com/yinhm/alphalake/internal/source/cninfo"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestCNINFOToPointInTimeFundamentalEndToEnd(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "pit-end-to-end.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	artifactRoot := filepath.Join(t.TempDir(), "raw")

	instrumentID, err := duckstore.UpsertInstrument(ctx, db,
		domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHE", Currency: "CNY", Name: "平安银行"},
		domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sz000001"},
	)
	if err != nil {
		t.Fatal(err)
	}

	china := time.FixedZone("Asia/Shanghai", 8*60*60)
	phase := "original"
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/new/hisAnnouncement/query":
			w.Header().Set("Content-Type", "application/json")
			if phase == "original" {
				providerDate := time.Date(2026, 3, 28, 0, 0, 0, 0, china)
				fmt.Fprintf(w, `{"pageNum":1,"pageSize":50,"totalpages":1,"totalRecordNum":1,"hasMore":false,"announcements":[{"announcementId":"original","secCode":"000001","secName":"平安银行","announcementTitle":"平安银行股份有限公司2025年年度报告","announcementTime":%d,"adjunctUrl":"finalpage/original.PDF","announcementType":"年度报告","columnId":"szse","pageColumn":"sz"}]}`, providerDate.UnixMilli())
				return
			}
			providerDate := time.Date(2026, 5, 10, 0, 0, 0, 0, china)
			fmt.Fprintf(w, `{"pageNum":1,"pageSize":50,"totalpages":1,"totalRecordNum":1,"hasMore":false,"announcements":[{"announcementId":"correction","secCode":"000001","secName":"平安银行","announcementTitle":"平安银行股份有限公司2025年年度报告（更正后）","announcementTime":%d,"adjunctUrl":"finalpage/correction.PDF","announcementType":"年度报告","columnId":"szse","pageColumn":"sz"}]}`, providerDate.UnixMilli())
		case "/finalpage/original.PDF":
			w.Header().Set("Content-Type", "application/pdf")
			_, _ = w.Write([]byte("%PDF-original"))
		case "/finalpage/correction.PDF":
			w.Header().Set("Content-Type", "application/pdf")
			_, _ = w.Write([]byte("%PDF-correction"))
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()

	cninfoClient, err := cninfo.NewClient(server.Client(), cninfo.ClientOptions{
		BaseURL: server.URL, DocumentBaseURL: server.URL + "/", Retries: 0,
	})
	if err != nil {
		t.Fatal(err)
	}

	originalSync, err := SyncCNINFOFilingsWithOptions(ctx, db, cninfoClient, artifactRoot, CNINFOFilingOptions{
		StartDate: time.Date(2026, 3, 28, 0, 0, 0, 0, time.UTC),
		EndDate:   time.Date(2026, 3, 28, 0, 0, 0, 0, time.UTC),
		Now:       func() time.Time { return time.Date(2026, 4, 1, 0, 0, 0, 0, time.UTC) },
	})
	if err != nil {
		t.Fatal(err)
	}
	if originalSync.Resolved != 1 || originalSync.Documents != 1 || originalSync.Pending != 0 {
		t.Fatalf("original filing sync=%#v", originalSync)
	}

	period := time.Date(2025, 12, 31, 0, 0, 0, 0, time.UTC)
	persistProviderRevision(t, ctx, db, artifactRoot, instrumentID, period, "provider-original", 100, time.Date(2026, 4, 1, 0, 0, 0, 0, time.UTC))
	firstMaterialization, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	if err != nil {
		t.Fatal(err)
	}
	if firstMaterialization.Linked != 1 || firstMaterialization.Inserted != 1 || firstMaterialization.Rejected != 0 {
		t.Fatalf("first materialization=%#v", firstMaterialization)
	}

	originalAvailable := time.Date(2026, 3, 29, 0, 0, 0, 0, china).UTC()
	assertPITRevenue(t, ctx, db, instrumentID, period, originalAvailable.Add(-time.Second), false, 0)
	assertPITRevenue(t, ctx, db, instrumentID, period, originalAvailable, true, 100)

	phase = "correction"
	correctionSync, err := SyncCNINFOFilingsWithOptions(ctx, db, cninfoClient, artifactRoot, CNINFOFilingOptions{
		StartDate: time.Date(2026, 5, 10, 0, 0, 0, 0, time.UTC),
		EndDate:   time.Date(2026, 5, 10, 0, 0, 0, 0, time.UTC),
		Now:       func() time.Time { return time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC) },
	})
	if err != nil {
		t.Fatal(err)
	}
	if correctionSync.Resolved != 1 || correctionSync.Documents != 1 || correctionSync.Pending != 0 {
		t.Fatalf("correction filing sync=%#v", correctionSync)
	}
	persistProviderRevision(t, ctx, db, artifactRoot, instrumentID, period, "provider-correction", 110, time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC))
	secondMaterialization, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	if err != nil {
		t.Fatal(err)
	}
	if secondMaterialization.Linked != 2 || secondMaterialization.Inserted != 1 || secondMaterialization.Rejected != 0 {
		t.Fatalf("second materialization=%#v", secondMaterialization)
	}

	correctionAvailable := time.Date(2026, 5, 11, 0, 0, 0, 0, china).UTC()
	assertPITRevenue(t, ctx, db, instrumentID, period, correctionAvailable.Add(-time.Second), true, 100)
	assertPITRevenue(t, ctx, db, instrumentID, period, correctionAvailable, true, 110)

	var originalLink, correctionLink string
	if err := db.QueryRowContext(ctx, `
		SELECT f.source_filing_id
		FROM fundamental.provider_filing_link l
		JOIN fundamental.filing f ON f.filing_id=l.filing_id
		WHERE l.provider_revision_key='provider-original'
	`).Scan(&originalLink); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRowContext(ctx, `
		SELECT f.source_filing_id
		FROM fundamental.provider_filing_link l
		JOIN fundamental.filing f ON f.filing_id=l.filing_id
		WHERE l.provider_revision_key='provider-correction'
	`).Scan(&correctionLink); err != nil {
		t.Fatal(err)
	}
	if originalLink != "original" || correctionLink != "correction" {
		t.Fatalf("provider links=%s/%s", originalLink, correctionLink)
	}

	var catalogueArtifacts, documentArtifacts, canonicalFacts int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM meta.artifact WHERE source='cninfo' AND dataset='filing_catalogue'`).Scan(&catalogueArtifacts); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM meta.artifact WHERE source='cninfo' AND dataset='filing_document'`).Scan(&documentArtifacts); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.fact WHERE canonical_field='revenue'`).Scan(&canonicalFacts); err != nil {
		t.Fatal(err)
	}
	if catalogueArtifacts != 2 || documentArtifacts != 2 || canonicalFacts != 2 {
		t.Fatalf("catalogue/document/facts=%d/%d/%d", catalogueArtifacts, documentArtifacts, canonicalFacts)
	}
}

func persistProviderRevision(t *testing.T, ctx context.Context, db *sql.DB, artifactRoot string, instrumentID int64, period time.Time, revision string, value float64, fetchedAt time.Time) {
	t.Helper()
	runID, err := duckstore.StartIngestRun(ctx, db, "tdx", "professional_financial", nil)
	if err != nil {
		t.Fatal(err)
	}
	stored, err := artifact.Persist(ctx, db, artifactRoot, artifact.Input{
		Source: "tdx", Dataset: "professional_financial",
		SourceLocator: "tdxfin/" + revision + ".zip", FetchedAt: fetchedAt,
		MediaType: "application/zip", ParserVersion: "test", IngestRunID: &runID,
		Content: []byte(revision),
	})
	if err != nil {
		t.Fatal(err)
	}
	record := domain.ProviderFinancialRecord{
		InstrumentID: instrumentID, Provider: "tdx", ProviderCode: "000001",
		ReportPeriod: period, SourceFile: revision + ".zip", ArtifactID: stored.ArtifactID,
		ProviderFields: make([]domain.ProviderFloat32, 230),
	}
	bits := math.Float32bits(float32(value))
	record.ProviderFields[229] = domain.ProviderFloat32{Bits: bits, Value: float64(math.Float32frombits(bits))}
	result, err := duckstore.ReconcileProviderFinancialRecordsForArtifact(ctx, db, runID, "tdx", revision, []domain.ProviderFinancialRecord{record})
	if err != nil {
		t.Fatal(err)
	}
	if result.Inserted != 230 {
		t.Fatalf("provider revision %s write=%#v", revision, result)
	}
	if err := duckstore.FinishIngestRun(ctx, db, runID, duckstore.IngestRunCompleted, nil); err != nil {
		t.Fatal(err)
	}
}

func assertPITRevenue(t *testing.T, ctx context.Context, db *sql.DB, instrumentID int64, period, asOf time.Time, want bool, wantValue float64) {
	t.Helper()
	var value float64
	err := db.QueryRowContext(ctx, `
		SELECT cast(value AS DOUBLE)
		FROM fundamental.fact_asof(?)
		WHERE instrument_id=? AND canonical_field='revenue' AND report_period=?
	`, asOf, instrumentID, period).Scan(&value)
	if !want {
		if err != sql.ErrNoRows {
			t.Fatalf("asof %s error=%v, want no row", asOf, err)
		}
		return
	}
	if err != nil {
		t.Fatalf("asof %s: %v", asOf, err)
	}
	if value != wantValue {
		t.Fatalf("asof %s value=%v, want %v", asOf, value, wantValue)
	}
}
