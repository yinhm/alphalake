package duckdb

import (
	"context"
	"fmt"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

func TestListFilingResolutionsPage(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "filing-query.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	period := time.Date(2000, 12, 31, 0, 0, 0, 0, time.UTC)
	for i := 1; i <= 5; i++ {
		filing := domain.FilingObservation{
			Source: "cninfo", SourceFilingID: fmt.Sprintf("filing-%d", i), ProviderCode: fmt.Sprintf("%06d", i),
			Title: "2000年年度报告", FilingType: domain.FilingTypeAnnual, FilingVariant: domain.FilingVariantFull,
			ReportPeriod: &period, AnnouncementDate: time.Date(2001, 3, i, 0, 0, 0, 0, time.UTC),
			AnnouncementTime:          time.Date(2001, 3, i, 16, 0, 0, 0, time.UTC),
			AnnouncementTimePrecision: domain.AnnouncementPrecisionDate,
			ClassifierVersion:         "test", ResolutionStatus: domain.FilingResolutionPending,
			ResolutionReason: "missing historical identity",
		}
		if _, err := UpsertFilings(ctx, db, 1, []domain.FilingObservation{filing}); err != nil {
			t.Fatal(err)
		}
	}
	first, err := ListFilingResolutionsPage(ctx, db, domain.FilingResolutionPending, 2, 0)
	if err != nil {
		t.Fatal(err)
	}
	second, err := ListFilingResolutionsPage(ctx, db, domain.FilingResolutionPending, 2, 2)
	if err != nil {
		t.Fatal(err)
	}
	third, err := ListFilingResolutionsPage(ctx, db, domain.FilingResolutionPending, 2, 4)
	if err != nil {
		t.Fatal(err)
	}
	if len(first) != 2 || len(second) != 2 || len(third) != 1 {
		t.Fatalf("page sizes=%d/%d/%d", len(first), len(second), len(third))
	}
	if first[0].SourceFilingID != "filing-1" || second[0].SourceFilingID != "filing-3" || third[0].SourceFilingID != "filing-5" {
		t.Fatalf("unexpected pages=%#v/%#v/%#v", first, second, third)
	}
	if first[0].AnnouncementTimePrecision != domain.AnnouncementPrecisionDate || first[0].AnnouncementDate == nil {
		t.Fatalf("precision/date=%s/%v", first[0].AnnouncementTimePrecision, first[0].AnnouncementDate)
	}
	if _, err := db.ExecContext(ctx, `UPDATE fundamental.filing SET announcement_time_precision=NULL WHERE source_filing_id='filing-5'`); err != nil {
		t.Fatal(err)
	}
	third, err = ListFilingResolutionsPage(ctx, db, domain.FilingResolutionPending, 2, 4)
	if err != nil {
		t.Fatal(err)
	}
	if len(third) != 1 || third[0].AnnouncementTimePrecision != domain.AnnouncementPrecisionTimestamp {
		t.Fatalf("null precision fallback=%#v", third)
	}
	refreshed, err := RefreshPendingFilingResolutions(ctx, db, 2, 2)
	if err != nil {
		t.Fatal(err)
	}
	if refreshed.Attempted != 5 || refreshed.StillPending != 5 {
		t.Fatalf("refresh=%#v", refreshed)
	}
	var precision string
	if err := db.QueryRowContext(ctx, `SELECT announcement_time_precision FROM fundamental.filing WHERE source_filing_id='filing-5'`).Scan(&precision); err != nil {
		t.Fatal(err)
	}
	if precision != domain.AnnouncementPrecisionTimestamp {
		t.Fatalf("stored precision=%q", precision)
	}
}
