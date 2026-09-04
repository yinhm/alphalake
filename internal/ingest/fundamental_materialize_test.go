package ingest

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestMaterializeProviderFundamentalsTracksCompletedAndPartialRuns(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "materialize-run.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	instrumentID, err := duckstore.UpsertInstrument(ctx, db,
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
	if _, err := duckstore.UpsertFilings(ctx, db, 1, []domain.FilingObservation{filing}); err != nil {
		t.Fatal(err)
	}
	var artifactID int64
	if err := db.QueryRowContext(ctx, `
		INSERT INTO meta.artifact (
			source, dataset, source_locator, fetched_at, sha256, content_length
		) VALUES ('tdx','professional_financial','tdxfin/test.zip',TIMESTAMPTZ '2026-04-01 00:00:00+00','revision',1)
		RETURNING artifact_id
	`).Scan(&artifactID); err != nil {
		t.Fatal(err)
	}
	if _, err := db.ExecContext(ctx, `
		INSERT INTO fundamental.provider_fact (
			instrument_id, source, report_period, provider_code, provider_field,
			value, artifact_id, revision_key
		) VALUES (?, 'tdx', ?, '600001', 'FN230', 100, ?, 'revision')
	`, instrumentID, period, artifactID); err != nil {
		t.Fatal(err)
	}

	summary, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	if err != nil {
		t.Fatal(err)
	}
	if summary.Linked != 1 || summary.Materialized != 1 || summary.Inserted != 1 || summary.LinkPending != 0 || summary.Rejected != 0 {
		t.Fatalf("summary=%#v", summary)
	}
	var status string
	if err := db.QueryRowContext(ctx, `SELECT status FROM meta.ingest_run WHERE ingest_run_id=?`, summary.RunID).Scan(&status); err != nil {
		t.Fatal(err)
	}
	if status != duckstore.IngestRunCompleted {
		t.Fatalf("run status=%s", status)
	}

	// A second provider record without filing evidence makes the next run partial,
	// while the already linked canonical fact remains available.
	var pendingArtifact int64
	if err := db.QueryRowContext(ctx, `
		INSERT INTO meta.artifact (
			source, dataset, source_locator, fetched_at, sha256, content_length
		) VALUES ('tdx','professional_financial','tdxfin/pending.zip',TIMESTAMPTZ '2026-04-01 00:00:00+00','pending-revision',1)
		RETURNING artifact_id
	`).Scan(&pendingArtifact); err != nil {
		t.Fatal(err)
	}
	if _, err := db.ExecContext(ctx, `
		INSERT INTO fundamental.provider_fact (
			instrument_id, source, report_period, provider_code, provider_field,
			value, artifact_id, revision_key
		) VALUES (?, 'tdx', DATE '2024-12-31', '600001', 'FN230', 90, ?, 'pending-revision')
	`, instrumentID, pendingArtifact); err != nil {
		t.Fatal(err)
	}
	partial, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	if err != nil {
		t.Fatal(err)
	}
	if partial.LinkPending != 1 {
		t.Fatalf("partial=%#v", partial)
	}
	if err := db.QueryRowContext(ctx, `SELECT status FROM meta.ingest_run WHERE ingest_run_id=?`, partial.RunID).Scan(&status); err != nil {
		t.Fatal(err)
	}
	if status != duckstore.IngestRunPartial {
		t.Fatalf("partial run status=%s", status)
	}
}
