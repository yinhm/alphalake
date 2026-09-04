package duckdb

import (
	"context"
	"database/sql"
	"math"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

func TestMaterializeCanonicalFundamentalsNoLookAheadAndCorrection(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "pit-fundamental.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	instrumentID, err := UpsertInstrument(ctx, db,
		domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHE", Currency: "CNY", Name: "平安银行"},
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
			InstrumentID: instrumentID, Source: "cninfo", SourceFilingID: "original", ProviderCode: "000001", ExchangeMIC: "XSHE",
			Title: "2025年年度报告", FilingType: domain.FilingTypeAnnual, FilingVariant: domain.FilingVariantFull,
			ReportPeriod: &period, AnnouncementTime: originalTime, ClassifierVersion: "test", ResolutionStatus: domain.FilingResolutionResolved,
		},
		{
			InstrumentID: instrumentID, Source: "cninfo", SourceFilingID: "correction", ProviderCode: "000001", ExchangeMIC: "XSHE",
			Title: "2025年年度报告（更正后）", FilingType: domain.FilingTypeAnnual, FilingVariant: domain.FilingVariantCorrectedReport,
			ReportPeriod: &period, AnnouncementTime: correctionTime, ClassifierVersion: "test", ResolutionStatus: domain.FilingResolutionResolved, IsCorrection: true,
		},
	}
	if _, err := UpsertFilings(ctx, db, 1, filings); err != nil {
		t.Fatal(err)
	}

	artifactA := insertTestArtifact(t, ctx, db, "annual-rev-a", time.Date(2026, 4, 1, 0, 0, 0, 0, time.UTC))
	artifactB := insertTestArtifact(t, ctx, db, "annual-rev-b", time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC))
	insertMappedProviderFact(t, ctx, db, artifactA, instrumentID, "annual-rev-a", "000001", "FN230", period, 100)
	insertMappedProviderFact(t, ctx, db, artifactB, instrumentID, "annual-rev-b", "000001", "FN230", period, 110)
	// Unreviewed provider fields remain provider evidence but never silently enter
	// the canonical fact layer.
	insertMappedProviderFact(t, ctx, db, artifactB, instrumentID, "annual-rev-b", "000001", "FN999", period, 999)

	links, err := RefreshProviderFilingLinks(ctx, db, 7, "tdx")
	if err != nil {
		t.Fatal(err)
	}
	if links.Records != 2 || links.Linked != 2 {
		t.Fatalf("links=%#v", links)
	}
	result, err := MaterializeCanonicalFundamentals(ctx, db, 8, "tdx")
	if err != nil {
		t.Fatal(err)
	}
	if result.Candidates != 2 || result.Materialized != 2 || result.Inserted != 2 || result.Updated != 0 || result.Removed != 0 || result.Rejected != 0 {
		t.Fatalf("materialize=%#v", result)
	}

	assertAsOfRevenue(t, ctx, db, instrumentID, period, time.Date(2026, 3, 28, 9, 59, 59, 0, time.UTC), false, 0)
	assertAsOfRevenue(t, ctx, db, instrumentID, period, time.Date(2026, 4, 1, 0, 0, 0, 0, time.UTC), true, 100)
	assertAsOfRevenue(t, ctx, db, instrumentID, period, time.Date(2026, 5, 10, 8, 59, 59, 0, time.UTC), true, 100)
	assertAsOfRevenue(t, ctx, db, instrumentID, period, time.Date(2026, 5, 10, 9, 0, 0, 0, time.UTC), true, 110)

	var latest float64
	var filingID int64
	if err := db.QueryRowContext(ctx, `
		SELECT cast(value AS DOUBLE), source_filing_id
		FROM fundamental.fact_latest
		WHERE instrument_id=? AND canonical_field='revenue' AND report_period=?
	`, instrumentID, period).Scan(&latest, &filingID); err != nil {
		t.Fatal(err)
	}
	if latest != 110 || filingID <= 0 {
		t.Fatalf("latest value/filing=%v/%d", latest, filingID)
	}

	replay, err := MaterializeCanonicalFundamentals(ctx, db, 9, "tdx")
	if err != nil {
		t.Fatal(err)
	}
	if replay.Inserted != 0 || replay.Updated != 0 || replay.Removed != 0 || replay.Materialized != 2 {
		t.Fatalf("replay=%#v", replay)
	}
	var canonicalRows, unreviewedRows int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.fact`).Scan(&canonicalRows); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.fact WHERE source_provider_field='FN999'`).Scan(&unreviewedRows); err != nil {
		t.Fatal(err)
	}
	if canonicalRows != 2 || unreviewedRows != 0 {
		t.Fatalf("canonical/unreviewed rows=%d/%d", canonicalRows, unreviewedRows)
	}
}

func TestMaterializeCanonicalFundamentalsRejectsInvalidAndRemovesStale(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "pit-reject.duckdb"))
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
	announcement := time.Date(2026, 3, 28, 10, 0, 0, 0, time.UTC)
	filing := domain.FilingObservation{
		InstrumentID: instrumentID, Source: "cninfo", SourceFilingID: "annual", ProviderCode: "600001", ExchangeMIC: "XSHG",
		Title: "2025年年度报告", FilingType: domain.FilingTypeAnnual, FilingVariant: domain.FilingVariantFull,
		ReportPeriod: &period, AnnouncementTime: announcement, ClassifierVersion: "test", ResolutionStatus: domain.FilingResolutionResolved,
	}
	if _, err := UpsertFilings(ctx, db, 1, []domain.FilingObservation{filing}); err != nil {
		t.Fatal(err)
	}
	artifactID := insertTestArtifact(t, ctx, db, "reject-rev", time.Date(2026, 4, 1, 0, 0, 0, 0, time.UTC))
	insertMappedProviderFact(t, ctx, db, artifactID, instrumentID, "reject-rev", "600001", "FN230", period, 100)
	if _, err := RefreshProviderFilingLinks(ctx, db, 2, "tdx"); err != nil {
		t.Fatal(err)
	}
	if _, err := MaterializeCanonicalFundamentals(ctx, db, 3, "tdx"); err != nil {
		t.Fatal(err)
	}

	// Provider records are immutable in normal operation; this mutation simulates
	// parser/catalogue correction turning a formerly materializable raw value into
	// unavailable evidence. Reconciliation must remove the stale canonical row.
	if _, err := db.ExecContext(ctx, `UPDATE fundamental.provider_fact SET value=? WHERE revision_key='reject-rev' AND provider_field='FN230'`, math.NaN()); err != nil {
		t.Fatal(err)
	}
	result, err := MaterializeCanonicalFundamentals(ctx, db, 4, "tdx")
	if err != nil {
		t.Fatal(err)
	}
	if result.Candidates != 1 || result.Rejected != 1 || result.Materialized != 0 || result.Removed != 1 {
		t.Fatalf("reject result=%#v", result)
	}
	var facts, diagnostics int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.fact`).Scan(&facts); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRowContext(ctx, `
		SELECT count(*) FROM meta.validation_result
		WHERE ingest_run_id=4 AND rule_code='provider_value_not_finite' AND passed=false
	`).Scan(&diagnostics); err != nil {
		t.Fatal(err)
	}
	if facts != 0 || diagnostics != 1 {
		t.Fatalf("facts/diagnostics=%d/%d", facts, diagnostics)
	}
}

func assertAsOfRevenue(t *testing.T, ctx context.Context, db *sql.DB, instrumentID int64, period, asOf time.Time, want bool, wantValue float64) {
	t.Helper()
	var value float64
	err := db.QueryRowContext(ctx, `
		SELECT cast(value AS DOUBLE)
		FROM fundamental.fact_asof(?)
		WHERE instrument_id=? AND canonical_field='revenue' AND report_period=?
	`, asOf, instrumentID, period).Scan(&value)
	if !want {
		if err != sql.ErrNoRows {
			t.Fatalf("asof %s error=%v, want no rows", asOf, err)
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

func insertMappedProviderFact(t *testing.T, ctx context.Context, db *sql.DB, artifactID, instrumentID int64, revision, code, field string, period time.Time, value float64) {
	t.Helper()
	if _, err := db.ExecContext(ctx, `
		INSERT INTO fundamental.provider_fact (
			instrument_id, source, report_period, provider_code, provider_field,
			value, value_float32_bits, artifact_id, revision_key
		) VALUES (?, 'tdx', ?, ?, ?, ?, ?, ?, ?)
	`, instrumentID, period, code, field, value, uint64(math.Float32bits(float32(value))), artifactID, revision); err != nil {
		t.Fatal(err)
	}
}
