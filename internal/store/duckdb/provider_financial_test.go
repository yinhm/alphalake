package duckdb

import (
	"context"
	"math"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

func TestInsertProviderFinancialRecordsPreservesRawBitsAndRevisions(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "provider-financial.duckdb"))
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
	runID, err := StartIngestRun(ctx, db, "tdx", "professional_financial", nil)
	if err != nil {
		t.Fatal(err)
	}
	period := time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC)
	bits1 := math.Float32bits(123.5)
	bits2 := uint32(0x80000000)
	record := domain.ProviderFinancialRecord{
		InstrumentID: instrumentID,
		Provider: "tdx", ProviderCode: "600001",
		ReportPeriod: period,
		ProviderFields: []domain.ProviderFloat32{
			{Bits: bits1, Value: float64(math.Float32frombits(bits1))},
			{Bits: bits2, Value: float64(math.Float32frombits(bits2))},
		},
		SourceFile: "gpcw20260630.zip", ArtifactID: 101,
	}
	first, err := InsertProviderFinancialRecordsForArtifact(ctx, db, runID, "sha-a", []domain.ProviderFinancialRecord{record})
	if err != nil {
		t.Fatal(err)
	}
	if first.Attempted != 2 || first.Inserted != 2 {
		t.Fatalf("first write=%#v, want attempted=2 inserted=2", first)
	}
	// Idempotent replay of the same immutable artifact must not look like new data.
	replay, err := InsertProviderFinancialRecordsForArtifact(ctx, db, runID, "sha-a", []domain.ProviderFinancialRecord{record})
	if err != nil {
		t.Fatal(err)
	}
	if replay.Attempted != 2 || replay.Inserted != 0 {
		t.Fatalf("replay=%#v, want attempted=2 inserted=0", replay)
	}

	var rows int
	var storedBits uint64
	if err := db.QueryRowContext(ctx, `
		SELECT count(*), max(value_float32_bits)
		FROM fundamental.provider_fact
		WHERE instrument_id=? AND revision_key='sha-a'
	`, instrumentID).Scan(&rows, &storedBits); err != nil {
		t.Fatal(err)
	}
	if rows != 2 || storedBits != uint64(bits2) {
		t.Fatalf("rows/bits=%d/%08x", rows, storedBits)
	}

	// Same report period from a corrected artifact is a separate revision.
	record.ArtifactID = 102
	record.ProviderFields[0] = domain.ProviderFloat32{Bits: math.Float32bits(124), Value: 124}
	corrected, err := InsertProviderFinancialRecordsForArtifact(ctx, db, runID, "sha-b", []domain.ProviderFinancialRecord{record})
	if err != nil {
		t.Fatal(err)
	}
	if corrected.Inserted != 2 {
		t.Fatalf("corrected write=%#v, want two new revision rows", corrected)
	}
	if err := db.QueryRowContext(ctx, `
		SELECT count(*) FROM fundamental.provider_fact
		WHERE instrument_id=? AND report_period=? AND provider_field='FN1'
	`, instrumentID, period).Scan(&rows); err != nil {
		t.Fatal(err)
	}
	if rows != 2 {
		t.Fatalf("FN1 revisions=%d, want 2", rows)
	}
}
