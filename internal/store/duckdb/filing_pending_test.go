package duckdb

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

func TestRefreshPendingFilingResolutionsAfterLifecycleEnrichment(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "pending-filing.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	period := time.Date(2000, 12, 31, 0, 0, 0, 0, time.UTC)
	announcementDate := time.Date(2001, 3, 1, 0, 0, 0, 0, time.UTC)
	announcement := time.Date(2001, 3, 2, 0, 0, 0, 0, domain.ChinaDisclosureLocation).UTC()
	pending := domain.FilingObservation{
		Source: "cninfo", SourceFilingID: "historical", ProviderCode: "430001", ExchangeMIC: "XBSE",
		Title: "2000年年度报告", FilingType: domain.FilingTypeAnnual, FilingVariant: domain.FilingVariantFull,
		ReportPeriod: &period, AnnouncementTime: announcement,
		AnnouncementDate: announcementDate, AnnouncementTimePrecision: domain.AnnouncementPrecisionDate,
		RawAnnouncementTimeMillis: announcement.Add(-24 * time.Hour).UnixMilli(),
		ClassifierVersion:         "test", ResolutionStatus: domain.FilingResolutionPending,
		ResolutionReason: "historical security lifecycle not yet available",
	}
	if _, err := UpsertFilings(ctx, db, 1, []domain.FilingObservation{pending}); err != nil {
		t.Fatal(err)
	}
	first, err := RefreshPendingFilingResolutions(ctx, db, 2, 10)
	if err != nil {
		t.Fatal(err)
	}
	if first.Attempted != 1 || first.Resolved != 0 || first.StillPending != 1 {
		t.Fatalf("first=%#v", first)
	}

	assertTiming := func() {
		t.Helper()
		var date, available time.Time
		var precision string
		var rawMillis int64
		if err := db.QueryRowContext(ctx, `SELECT announcement_date, announcement_time, announcement_time_precision, raw_announcement_time_ms
			FROM fundamental.filing WHERE source_filing_id='historical'`).Scan(&date, &available, &precision, &rawMillis); err != nil {
			t.Fatal(err)
		}
		if !date.Equal(announcementDate) || !available.Equal(announcement) || precision != domain.AnnouncementPrecisionDate || rawMillis != pending.RawAnnouncementTimeMillis {
			t.Fatalf("timing changed: %s / %s / %s / %d", date, available, precision, rawMillis)
		}
	}
	assertTiming()

	validTo := announcementDate.AddDate(0, 0, 1)
	validFrom := time.Date(1999, 1, 1, 0, 0, 0, 0, time.UTC)
	instrumentID, err := UpsertInstrument(ctx, db,
		domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XBSE", Currency: "CNY", Name: "Historical"},
		domain.Identifier{Provider: "tdx", Type: "symbol", Value: "bj430001", ValidFrom: &validFrom, ValidTo: &validTo},
	)
	if err != nil {
		t.Fatal(err)
	}
	second, err := RefreshPendingFilingResolutions(ctx, db, 3, 10)
	if err != nil {
		t.Fatal(err)
	}
	if second.Attempted != 1 || second.Resolved != 1 || second.StillPending != 0 {
		t.Fatalf("second=%#v", second)
	}
	assertTiming()
	var storedID int64
	var status, reason string
	if err := db.QueryRowContext(ctx, `
		SELECT instrument_id, resolution_status, COALESCE(resolution_reason,'')
		FROM fundamental.filing WHERE source_filing_id='historical'
	`).Scan(&storedID, &status, &reason); err != nil {
		t.Fatal(err)
	}
	if storedID != instrumentID || status != domain.FilingResolutionResolved || reason != "" {
		t.Fatalf("stored=%d/%s/%q", storedID, status, reason)
	}
}
