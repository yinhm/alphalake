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

type fakeTDXClassificationSource struct {
	instruments []domain.InstrumentObservation
	families    []string
	snapshots   map[string]domain.ClassificationSnapshot
	errors      map[string]error
}

func (f *fakeTDXClassificationSource) Instruments(context.Context) ([]domain.InstrumentObservation, error) {
	return f.instruments, nil
}

func (f *fakeTDXClassificationSource) ClassificationFamilies() []string {
	return append([]string(nil), f.families...)
}

func (f *fakeTDXClassificationSource) ClassificationSnapshot(_ context.Context, family string) (domain.ClassificationSnapshot, error) {
	if err := f.errors[family]; err != nil {
		return domain.ClassificationSnapshot{}, err
	}
	return f.snapshots[family], nil
}

func fakeClassificationSnapshot(code, typ, nodeCode string, members ...string) domain.ClassificationSnapshot {
	taxonomy := domain.ClassificationTaxonomy{Source: "tdx", Code: code, Name: code, Type: typ}
	ids := make([]domain.Identifier, 0, len(members))
	for _, symbol := range members {
		ids = append(ids, domain.Identifier{Provider: "tdx", Type: "symbol", Value: symbol})
	}
	return domain.ClassificationSnapshot{
		Taxonomy: taxonomy,
		Nodes: []domain.ClassificationNodeObservation{{
			Taxonomy: taxonomy, SourceNodeCode: nodeCode, Name: nodeCode, Level: 1, Members: ids,
		}},
		Complete: true,
	}
}

func TestSyncTDXClassificationsKeepsSuccessfulFamiliesOnPartialFailure(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "classification.duckdb"))
	if err != nil {
		t.Fatalf("OpenAndMigrate() error = %v", err)
	}
	defer db.Close()

	instruments := []domain.InstrumentObservation{
		observation(domain.InstrumentEquity, "XSHG", "A", "sh600001"),
		observation(domain.InstrumentEquity, "XSHE", "B", "sz000001"),
	}
	concept := fakeClassificationSnapshot("tdx_concept", "concept", "880500", "sh600001", "sz000001")
	source := &fakeTDXClassificationSource{
		instruments: instruments,
		families: []string{"tdx_concept", "tdx_style_region"},
		snapshots: map[string]domain.ClassificationSnapshot{"tdx_concept": concept},
		errors: map[string]error{"tdx_style_region": errors.New("TDX block unavailable")},
	}

	// 2026-09-03 17:30 UTC is already 2026-09-04 in China.
	now := time.Date(2026, 9, 3, 17, 30, 0, 0, time.UTC)
	summary, err := SyncTDXClassificationsWithOptions(ctx, db, source, TDXClassificationSyncOptions{Now: func() time.Time { return now }})
	var batchErr *TDXClassificationBatchError
	if !errors.As(err, &batchErr) {
		t.Fatalf("error = %v, want TDXClassificationBatchError", err)
	}
	if summary.RunID <= 0 || summary.Families != 2 || summary.Synced != 1 || len(summary.Failures) != 1 || summary.Members != 2 || summary.Opened != 2 {
		t.Fatalf("summary = %#v", summary)
	}

	var status string
	if err := db.QueryRowContext(ctx, `SELECT status FROM meta.ingest_run WHERE ingest_run_id=?`, summary.RunID).Scan(&status); err != nil {
		t.Fatalf("query run: %v", err)
	}
	if status != duckstore.IngestRunPartial {
		t.Fatalf("status = %q, want partial", status)
	}

	var count int
	var minDate time.Time
	if err := db.QueryRowContext(ctx, `
		SELECT count(*), min(effective_from)
		FROM classification.membership m
		JOIN classification.node n ON n.node_id=m.node_id
		JOIN classification.taxonomy t ON t.taxonomy_id=n.taxonomy_id
		WHERE t.taxonomy_code='tdx_concept' AND m.effective_to IS NULL
	`).Scan(&count, &minDate); err != nil {
		t.Fatalf("query memberships: %v", err)
	}
	wantDate := time.Date(2026, 9, 4, 0, 0, 0, 0, time.UTC)
	if count != 2 || !minDate.Equal(wantDate) {
		t.Fatalf("membership count/date = %d/%v, want 2/%v", count, minDate, wantDate)
	}
}
