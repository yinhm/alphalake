package duckdb

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

func TestApplyClassificationSnapshotTreatsSameDayRemovalAsCorrection(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "same-day.duckdb"))
	if err != nil {
		t.Fatalf("OpenAndMigrate() error = %v", err)
	}
	defer db.Close()

	if _, err := UpsertInstrument(ctx, db,
		domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHG", Currency: "CNY", Name: "Test"},
		domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600001"},
	); err != nil {
		t.Fatalf("UpsertInstrument() error = %v", err)
	}

	day := time.Date(2026, 9, 4, 0, 0, 0, 0, time.UTC)
	run1, _ := StartIngestRun(ctx, db, "tdx", "classification", nil)
	if _, err := ApplyClassificationSnapshotForRun(ctx, db, run1, day, day.Add(10*time.Hour), classificationSnapshot("sh600001")); err != nil {
		t.Fatalf("first snapshot error = %v", err)
	}

	empty := classificationSnapshot()
	run2, _ := StartIngestRun(ctx, db, "tdx", "classification", nil)
	result, err := ApplyClassificationSnapshotForRun(ctx, db, run2, day, day.Add(12*time.Hour), empty)
	if err != nil {
		t.Fatalf("same-day correction error = %v", err)
	}
	if result.Closed != 1 {
		t.Fatalf("result = %#v", result)
	}

	var rows int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM classification.membership`).Scan(&rows); err != nil {
		t.Fatalf("count memberships: %v", err)
	}
	if rows != 0 {
		t.Fatalf("membership rows = %d, want 0 after same-day correction", rows)
	}
}
