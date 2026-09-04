package duckdb

import (
	"context"
	"fmt"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

func TestInstrumentSnapshotPreflightRejectsFlatPartitionDriftBeforeWrites(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "preflight.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	flat := []domain.InstrumentObservation{snapshotObservation("sh600001", "A")}
	partition := []domain.InstrumentObservation{
		flat[0],
		snapshotObservation("sh600002", "B"), // absent from flat snapshot
	}
	_, err = ApplyInstrumentMasterSnapshot(ctx, db, domain.InstrumentMasterSnapshot{
		Source: "tdx",
		AsOfDate: time.Date(2026, 9, 4, 0, 0, 0, 0, time.UTC),
		Complete: true,
		Observations: flat,
		Partitions: []domain.InstrumentMasterPartition{{
			Key: "sh", ExchangeMIC: "XSHG", Complete: true, Observations: partition,
		}},
	})
	if err == nil {
		t.Fatal("expected flat/partition preflight error")
	}

	var instruments, identifiers int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM ref.instrument`).Scan(&instruments); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM ref.instrument_identifier`).Scan(&identifiers); err != nil {
		t.Fatal(err)
	}
	if instruments != 0 || identifiers != 0 {
		t.Fatalf("preflight left side effects: instruments=%d identifiers=%d", instruments, identifiers)
	}
}

func TestLegacyInstrumentSnapshotRetainsGlobalTruncationGuard(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "legacy-guard.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	makeObservations := func(n int) []domain.InstrumentObservation {
		out := make([]domain.InstrumentObservation, 0, n)
		for i := 0; i < n; i++ {
			out = append(out, snapshotObservation(fmt.Sprintf("sh%06d", 600000+i), fmt.Sprintf("S%d", i)))
		}
		return out
	}
	day1 := time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC)
	if _, err := ApplyInstrumentMasterSnapshot(ctx, db, domain.InstrumentMasterSnapshot{
		Source: "tdx", AsOfDate: day1, Complete: true, Observations: makeObservations(100),
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := ApplyInstrumentMasterSnapshot(ctx, db, domain.InstrumentMasterSnapshot{
		Source: "tdx", AsOfDate: day1.AddDate(0, 0, 1), Complete: true, Observations: makeObservations(50),
	}); err == nil {
		t.Fatal("expected legacy global truncation guard error")
	}

	var open, pending int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM ref.instrument_identifier WHERE provider='tdx' AND valid_to IS NULL`).Scan(&open); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM meta.checkpoint WHERE source='tdx' AND dataset='instrument_master'`).Scan(&pending); err != nil {
		t.Fatal(err)
	}
	if open != 100 || pending != 0 {
		t.Fatalf("legacy truncation changed state: open=%d pending=%d", open, pending)
	}
}
