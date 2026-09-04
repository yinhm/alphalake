package ingest

import (
	"context"
	"errors"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

type fakeIndustrySource struct {
	instruments []domain.InstrumentObservation
	results     []domain.ClassificationSnapshotResult
}

func (f *fakeIndustrySource) Instruments(context.Context) ([]domain.InstrumentObservation, error) {
	return f.instruments, nil
}

func (f *fakeIndustrySource) IndustrySnapshotResults(context.Context) ([]domain.ClassificationSnapshotResult, error) {
	return f.results, nil
}

func industrySnapshot(code, name, nodeCode string, member domain.Identifier) domain.ClassificationSnapshot {
	taxonomy := domain.ClassificationTaxonomy{Source: "tdx", Code: code, Name: name, Type: "industry"}
	return domain.ClassificationSnapshot{
		Taxonomy: taxonomy,
		Nodes: []domain.ClassificationNodeObservation{{
			Taxonomy: taxonomy, SourceNodeCode: nodeCode, Name: name + " leaf", Level: 1,
			Members: []domain.Identifier{member},
		}},
		Complete: true,
	}
}

func resultForSnapshot(snapshot domain.ClassificationSnapshot) domain.ClassificationSnapshotResult {
	copy := snapshot
	return domain.ClassificationSnapshotResult{Code: snapshot.Taxonomy.Code, Snapshot: &copy}
}

func TestSyncTDXIndustriesPersistsBothTaxonomies(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "industry.duckdb"))
	if err != nil { t.Fatal(err) }
	defer db.Close()

	instrument := observation(domain.InstrumentEquity, "XSHG", "Test", "sh600001")
	member := domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600001"}
	source := &fakeIndustrySource{
		instruments: []domain.InstrumentObservation{instrument},
		results: []domain.ClassificationSnapshotResult{
			resultForSnapshot(industrySnapshot("tdx_industry", "TDX Industry", "T010101", member)),
			resultForSnapshot(industrySnapshot("tdx_shenwan_industry", "Shenwan Industry", "X010101", member)),
		},
	}
	now := time.Date(2026, 9, 4, 3, 0, 0, 0, time.UTC)
	summary, err := SyncTDXIndustriesWithOptions(ctx, db, source, TDXIndustrySyncOptions{Now: func() time.Time { return now }})
	if err != nil { t.Fatal(err) }
	if summary.RunID <= 0 || summary.Taxonomies != 2 || summary.Synced != 2 || summary.Nodes != 2 || summary.Members != 2 || summary.Opened != 2 {
		t.Fatalf("summary=%#v", summary)
	}

	var memberships int
	if err := db.QueryRowContext(ctx, `
		SELECT count(*)
		FROM classification.membership m
		JOIN classification.node n ON n.node_id=m.node_id
		JOIN classification.taxonomy t ON t.taxonomy_id=n.taxonomy_id
		WHERE t.taxonomy_code IN ('tdx_industry','tdx_shenwan_industry')
		  AND m.effective_to IS NULL
	`).Scan(&memberships); err != nil { t.Fatal(err) }
	if memberships != 2 { t.Fatalf("memberships=%d, want 2", memberships) }
	var status string
	if err := db.QueryRowContext(ctx, `SELECT status FROM meta.ingest_run WHERE ingest_run_id=?`, summary.RunID).Scan(&status); err != nil { t.Fatal(err) }
	if status != duckstore.IngestRunCompleted { t.Fatalf("status=%q, want completed", status) }
}

func TestSyncTDXIndustriesKeepsSuccessfulTaxonomyWhenOtherBuildFails(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "industry-partial.duckdb"))
	if err != nil { t.Fatal(err) }
	defer db.Close()
	instrument := observation(domain.InstrumentEquity, "XSHG", "Test", "sh600001")
	member := domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600001"}
	good := industrySnapshot("tdx_shenwan_industry", "Shenwan Industry", "X010101", member)
	source := &fakeIndustrySource{
		instruments: []domain.InstrumentObservation{instrument},
		results: []domain.ClassificationSnapshotResult{
			{Code: "tdx_industry", Error: "malformed TDX industry code"},
			resultForSnapshot(good),
		},
	}
	summary, err := SyncTDXIndustriesWithOptions(ctx, db, source, TDXIndustrySyncOptions{Now: func() time.Time {
		return time.Date(2026, 9, 4, 3, 0, 0, 0, time.UTC)
	}})
	var batchErr *TDXIndustryBatchError
	if !errors.As(err, &batchErr) {
		t.Fatalf("err=%v, want TDXIndustryBatchError", err)
	}
	if summary.Synced != 1 || len(summary.Failures) != 1 || summary.Failures[0].Family != "tdx_industry" {
		t.Fatalf("summary=%#v", summary)
	}
	var memberships int
	if err := db.QueryRowContext(ctx, `
		SELECT count(*) FROM classification.membership m
		JOIN classification.node n ON n.node_id=m.node_id
		JOIN classification.taxonomy t ON t.taxonomy_id=n.taxonomy_id
		WHERE t.taxonomy_code='tdx_shenwan_industry' AND m.effective_to IS NULL
	`).Scan(&memberships); err != nil { t.Fatal(err) }
	if memberships != 1 { t.Fatalf("successful taxonomy memberships=%d, want 1", memberships) }
	var status string
	if err := db.QueryRowContext(ctx, `SELECT status FROM meta.ingest_run WHERE ingest_run_id=?`, summary.RunID).Scan(&status); err != nil { t.Fatal(err) }
	if status != duckstore.IngestRunPartial { t.Fatalf("status=%q, want partial", status) }
}
