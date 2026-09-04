package duckdb

import (
	"context"
	"fmt"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

func snapshotObservation(symbol, name string) domain.InstrumentObservation {
	return domain.InstrumentObservation{
		Instrument: domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHG", Currency: "CNY", Name: name},
		Identifier: domain.Identifier{Provider: "tdx", Type: "symbol", Value: symbol},
	}
}

func TestApplyInstrumentMasterSnapshotClosesMissingAndSeparatesCodeReuse(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "snapshot.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	day1 := time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC)
	first, err := ApplyInstrumentMasterSnapshot(ctx, db, domain.InstrumentMasterSnapshot{
		Source: "tdx", AsOfDate: day1, Complete: true,
		Observations: []domain.InstrumentObservation{
			snapshotObservation("sh600001", "Old A"),
			snapshotObservation("sh600002", "B"),
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	oldA := first.InstrumentIDs[0]

	day2 := day1.AddDate(0, 0, 1)
	second, err := ApplyInstrumentMasterSnapshot(ctx, db, domain.InstrumentMasterSnapshot{
		Source: "tdx", AsOfDate: day2, Complete: true,
		Observations: []domain.InstrumentObservation{snapshotObservation("sh600002", "B")},
	})
	if err != nil {
		t.Fatal(err)
	}
	if second.Closed != 1 {
		t.Fatalf("closed=%d, want 1", second.Closed)
	}

	var validTo time.Time
	if err := db.QueryRowContext(ctx, `
		SELECT valid_to FROM ref.instrument_identifier
		WHERE instrument_id=? AND provider='tdx' AND identifier_value='sh600001'
	`, oldA).Scan(&validTo); err != nil {
		t.Fatal(err)
	}
	if !dateUTC(validTo).Equal(day2) {
		t.Fatalf("old identifier valid_to=%v, want %v", validTo, day2)
	}

	day3 := day2.AddDate(0, 0, 1)
	third, err := ApplyInstrumentMasterSnapshot(ctx, db, domain.InstrumentMasterSnapshot{
		Source: "tdx", AsOfDate: day3, Complete: true,
		Observations: []domain.InstrumentObservation{
			snapshotObservation("sh600001", "New A"),
			snapshotObservation("sh600002", "B"),
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	newA := third.InstrumentIDs[0]
	if newA == oldA {
		t.Fatalf("reused code resolved to old instrument %d", oldA)
	}

	resolvedOld, ok, err := ResolveInstrumentIdentifierAt(ctx, db, "tdx", "symbol", "sh600001", day1)
	if err != nil || !ok || resolvedOld != oldA {
		t.Fatalf("historical resolve=%d/%v/%v, want %d/true/nil", resolvedOld, ok, err, oldA)
	}
	resolvedNew, ok, err := ResolveInstrumentIdentifierAt(ctx, db, "tdx", "symbol", "sh600001", day3)
	if err != nil || !ok || resolvedNew != newA {
		t.Fatalf("current resolve=%d/%v/%v, want %d/true/nil", resolvedNew, ok, err, newA)
	}
}

func TestApplyInstrumentMasterSnapshotIncompleteDoesNotCloseMissing(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "partial.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	day1 := time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC)
	if _, err := ApplyInstrumentMasterSnapshot(ctx, db, domain.InstrumentMasterSnapshot{
		Source: "tdx", AsOfDate: day1, Complete: true,
		Observations: []domain.InstrumentObservation{
			snapshotObservation("sh600001", "A"), snapshotObservation("sh600002", "B"),
		},
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := ApplyInstrumentMasterSnapshot(ctx, db, domain.InstrumentMasterSnapshot{
		Source: "tdx", AsOfDate: day1.AddDate(0, 0, 1), Complete: false,
		Observations: []domain.InstrumentObservation{snapshotObservation("sh600002", "B")},
	}); err != nil {
		t.Fatal(err)
	}
	var open int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM ref.instrument_identifier WHERE provider='tdx' AND valid_to IS NULL`).Scan(&open); err != nil {
		t.Fatal(err)
	}
	if open != 2 {
		t.Fatalf("open identifiers=%d, want 2 after incomplete snapshot", open)
	}
}

func TestApplyInstrumentMasterSnapshotRejectsLargeTruncation(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "truncated.duckdb"))
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
		t.Fatal("expected suspicious truncation error")
	}
	var open int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM ref.instrument_identifier WHERE provider='tdx' AND valid_to IS NULL`).Scan(&open); err != nil {
		t.Fatal(err)
	}
	if open != 100 {
		t.Fatalf("open identifiers=%d, want rollback to 100", open)
	}
}
