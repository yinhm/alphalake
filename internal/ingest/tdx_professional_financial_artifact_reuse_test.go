package ingest

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestFinancialManifestRollbackReusesOlderRetainedArtifact(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "rollback.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	root := filepath.Join(t.TempDir(), "raw")
	source := &fakeProfessionalFinancialSource{
		instruments: []domain.InstrumentObservation{{
			Instrument: domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHG", Currency: "CNY", Name: "Test"},
			Identifier: domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600001"},
		}},
		packageBytes: []byte("zipA"), recordCode: "600001",
	}
	options := TDXProfessionalFinancialOptions{MaxPackages: 1, Now: func() time.Time {
		return time.Date(2026, 9, 4, 12, 0, 0, 0, time.UTC)
	}}
	first, err := SyncTDXProfessionalFinancialWithOptions(ctx, db, source, root, options)
	if err != nil {
		t.Fatal(err)
	}
	if first.FactsInserted != 2 || source.packageCalls != 1 {
		t.Fatalf("first=%#v calls=%d", first, source.packageCalls)
	}

	source.packageBytes = []byte("zipB")
	second, err := SyncTDXProfessionalFinancialWithOptions(ctx, db, source, root, options)
	if err != nil {
		t.Fatal(err)
	}
	if second.FactsInserted != 2 || source.packageCalls != 2 {
		t.Fatalf("second=%#v calls=%d, corrected package should download once", second, source.packageCalls)
	}

	// Provider manifest rolls back to previously-seen bytes. The old artifact is
	// already in the content-addressed lake, so no third network package fetch is
	// necessary. Its provider facts are an idempotent replay of the old revision.
	source.packageBytes = []byte("zipA")
	third, err := SyncTDXProfessionalFinancialWithOptions(ctx, db, source, root, options)
	if err != nil {
		t.Fatal(err)
	}
	if source.packageCalls != 2 {
		t.Fatalf("package calls=%d, want historical artifact reuse without redownload", source.packageCalls)
	}
	if third.FactsAttempted != 2 || third.FactsInserted != 0 {
		t.Fatalf("third=%#v, want attempted replay with zero new facts", third)
	}
}
