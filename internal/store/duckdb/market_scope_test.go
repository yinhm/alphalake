package duckdb

import (
	"context"
	"github.com/yinhm/alphalake/internal/domain"
	"math"
	"path/filepath"
	"testing"
	"time"
)

func TestDefaultMarketScopePreservesExcludedEvidence(t *testing.T) {
	ctx := context.Background()
	explicit := domain.WithBSE(ctx)
	db, err := OpenInitialized(ctx, filepath.Join(t.TempDir(), "scope.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	id, err := UpsertInstrument(ctx, db, domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XBSE", Currency: "CNY", Name: "Beijing"}, domain.Identifier{Provider: "tdx", Type: "symbol", Value: "bj920001"})
	if err != nil {
		t.Fatal(err)
	}
	run, err := StartIngestRun(ctx, db, "tdx", "professional_financial", nil)
	if err != nil {
		t.Fatal(err)
	}
	end := time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC)
	record := domain.ProviderFinancialRecord{InstrumentID: id, Provider: "tdx", ProviderCode: "920001", ReportPeriod: end, ArtifactID: 201, SourceFile: "gpcw20260630.zip", ProviderFields: []domain.ProviderFloat32{{Bits: math.Float32bits(10), Value: 10}}}
	write, err := ReconcileProviderFinancialRecordsForArtifact(ctx, db, run, "tdx", "scope-sha", []domain.ProviderFinancialRecord{record})
	if err != nil || write.Inserted != 0 || write.Attempted != 0 {
		t.Fatal(write, err)
	}
	write, err = ReconcileProviderFinancialRecordsForArtifact(explicit, db, run, "tdx", "scope-sha", []domain.ProviderFinancialRecord{record})
	if err != nil || write.Inserted != 1 {
		t.Fatal(write, err)
	}
	write, err = ReconcileProviderFinancialRecordsForArtifact(ctx, db, run, "tdx", "scope-sha", nil)
	if err != nil || write.Removed != 0 {
		t.Fatal(write, err)
	}
	var count int
	if err := db.QueryRowContext(ctx, "SELECT count(*) FROM fundamental.provider_fact").Scan(&count); err != nil || count != 1 {
		t.Fatal(count, err)
	}
	snapshot := classificationSnapshot("bj920001")
	applied, err := ApplyClassificationSnapshotForRun(explicit, db, run, end, end, snapshot)
	if err != nil || applied.Opened != 1 {
		t.Fatal(applied, err)
	}
	applied, err = ApplyClassificationSnapshotForRun(ctx, db, run, end.AddDate(0, 0, 1), end.AddDate(0, 0, 1), snapshot)
	if err != nil || applied.Members != 0 || applied.Closed != 0 || applied.Unresolved != 0 {
		t.Fatal(applied, err)
	}
	for _, tc := range []struct {
		ctx   context.Context
		count int
	}{{ctx, 0}, {explicit, 1}} {
		result, err := ExportValuationReadiness(tc.ctx, db, end, end.AddDate(0, 1, 0))
		if err != nil || result["universe_count"] != tc.count {
			t.Fatal(result, err)
		}
	}
	result, err := ExportCompanyValuationReadiness(ctx, db, end, end.AddDate(0, 1, 0), "920001")
	if err != nil || result["universe_count"] != 1 {
		t.Fatal(result, err)
	}
}
