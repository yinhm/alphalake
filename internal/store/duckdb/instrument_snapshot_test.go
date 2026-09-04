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
	mic := "XSHG"
	if len(symbol) >= 2 && symbol[:2] == "bj" {
		mic = "XBSE"
	} else if len(symbol) >= 2 && symbol[:2] == "sz" {
		mic = "XSHE"
	}
	return domain.InstrumentObservation{
		Instrument: domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: mic, Currency: "CNY", Name: name},
		Identifier: domain.Identifier{Provider: "tdx", Type: "symbol", Value: symbol},
	}
}

func TestApplyInstrumentMasterSnapshotRequiresRepeatedAbsenceBeforeCodeReuse(t *testing.T) {
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
	if second.Closed != 0 || second.PendingClose != 1 {
		t.Fatalf("second result=%#v, want one pending close and no closure", second)
	}

	// Same-day rerun is not additional evidence.
	sameDay, err := ApplyInstrumentMasterSnapshot(ctx, db, domain.InstrumentMasterSnapshot{
		Source: "tdx", AsOfDate: day2, Complete: true,
		Observations: []domain.InstrumentObservation{snapshotObservation("sh600002", "B")},
	})
	if err != nil {
		t.Fatal(err)
	}
	if sameDay.Closed != 0 {
		t.Fatalf("same-day rerun closed=%d, want 0", sameDay.Closed)
	}

	day3 := day2.AddDate(0, 0, 1)
	third, err := ApplyInstrumentMasterSnapshot(ctx, db, domain.InstrumentMasterSnapshot{
		Source: "tdx", AsOfDate: day3, Complete: true,
		Observations: []domain.InstrumentObservation{snapshotObservation("sh600002", "B")},
	})
	if err != nil {
		t.Fatal(err)
	}
	if third.Closed != 1 {
		t.Fatalf("third closed=%d, want 1", third.Closed)
	}

	var validTo time.Time
	if err := db.QueryRowContext(ctx, `
		SELECT valid_to FROM ref.instrument_identifier
		WHERE instrument_id=? AND provider='tdx' AND identifier_value='sh600001'
	`, oldA).Scan(&validTo); err != nil {
		t.Fatal(err)
	}
	if !dateUTC(validTo).Equal(day2) {
		t.Fatalf("old identifier valid_to=%v, want first missing day %v", validTo, day2)
	}

	day4 := day3.AddDate(0, 0, 1)
	fourth, err := ApplyInstrumentMasterSnapshot(ctx, db, domain.InstrumentMasterSnapshot{
		Source: "tdx", AsOfDate: day4, Complete: true,
		Observations: []domain.InstrumentObservation{
			snapshotObservation("sh600001", "New A"),
			snapshotObservation("sh600002", "B"),
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	newA := fourth.InstrumentIDs[0]
	if newA == oldA {
		t.Fatalf("reused code resolved to old instrument %d", oldA)
	}

	resolvedOld, ok, err := ResolveInstrumentIdentifierAt(ctx, db, "tdx", "symbol", "sh600001", day1)
	if err != nil || !ok || resolvedOld != oldA {
		t.Fatalf("historical resolve=%d/%v/%v, want %d/true/nil", resolvedOld, ok, err, oldA)
	}
	resolvedNew, ok, err := ResolveInstrumentIdentifierAt(ctx, db, "tdx", "symbol", "sh600001", day4)
	if err != nil || !ok || resolvedNew != newA {
		t.Fatalf("current resolve=%d/%v/%v, want %d/true/nil", resolvedNew, ok, err, newA)
	}
}

func TestApplyInstrumentMasterSnapshotReturnClearsPendingAbsence(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "return.duckdb"))
	if err != nil { t.Fatal(err) }
	defer db.Close()
	day1 := time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC)
	both := []domain.InstrumentObservation{snapshotObservation("sh600001", "A"), snapshotObservation("sh600002", "B")}
	if _, err := ApplyInstrumentMasterSnapshot(ctx, db, domain.InstrumentMasterSnapshot{Source:"tdx", AsOfDate:day1, Complete:true, Observations:both}); err != nil { t.Fatal(err) }
	if _, err := ApplyInstrumentMasterSnapshot(ctx, db, domain.InstrumentMasterSnapshot{Source:"tdx", AsOfDate:day1.AddDate(0,0,1), Complete:true, Observations:both[1:]}); err != nil { t.Fatal(err) }
	if _, err := ApplyInstrumentMasterSnapshot(ctx, db, domain.InstrumentMasterSnapshot{Source:"tdx", AsOfDate:day1.AddDate(0,0,2), Complete:true, Observations:both}); err != nil { t.Fatal(err) }
	var missingEvidence int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM meta.checkpoint WHERE source='tdx' AND dataset='instrument_master'`).Scan(&missingEvidence); err != nil { t.Fatal(err) }
	if missingEvidence != 0 { t.Fatalf("missing evidence rows=%d, want 0 after return", missingEvidence) }
	var open int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM ref.instrument_identifier WHERE provider='tdx' AND valid_to IS NULL`).Scan(&open); err != nil { t.Fatal(err) }
	if open != 2 { t.Fatalf("open identifiers=%d, want 2", open) }
}

func TestApplyInstrumentMasterSnapshotIncompleteDoesNotCloseMissing(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "partial.duckdb"))
	if err != nil { t.Fatal(err) }
	defer db.Close()
	day1 := time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC)
	if _, err := ApplyInstrumentMasterSnapshot(ctx, db, domain.InstrumentMasterSnapshot{
		Source: "tdx", AsOfDate: day1, Complete: true,
		Observations: []domain.InstrumentObservation{snapshotObservation("sh600001", "A"), snapshotObservation("sh600002", "B")},
	}); err != nil { t.Fatal(err) }
	if _, err := ApplyInstrumentMasterSnapshot(ctx, db, domain.InstrumentMasterSnapshot{
		Source: "tdx", AsOfDate: day1.AddDate(0, 0, 1), Complete: false,
		Observations: []domain.InstrumentObservation{snapshotObservation("sh600002", "B")},
	}); err != nil { t.Fatal(err) }
	var open int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM ref.instrument_identifier WHERE provider='tdx' AND valid_to IS NULL`).Scan(&open); err != nil { t.Fatal(err) }
	if open != 2 { t.Fatalf("open identifiers=%d, want 2 after incomplete snapshot", open) }
}

func TestApplyInstrumentMasterSnapshotScopesAuthorityByPartition(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "partitioned.duckdb"))
	if err != nil { t.Fatal(err) }
	defer db.Close()
	day1 := time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC)
	initial := []domain.InstrumentObservation{snapshotObservation("sh600001", "SH-A"), snapshotObservation("sh600002", "SH-B"), snapshotObservation("bj920001", "BJ-A")}
	if _, err := ApplyInstrumentMasterSnapshot(ctx, db, domain.InstrumentMasterSnapshot{Source:"tdx", AsOfDate:day1, Complete:true, Observations:initial}); err != nil { t.Fatal(err) }

	shOnly := []domain.InstrumentObservation{snapshotObservation("sh600002", "SH-B")}
	partitioned := func(day time.Time) domain.InstrumentMasterSnapshot {
		return domain.InstrumentMasterSnapshot{
			Source:"tdx", AsOfDate:day, Complete:false, Observations:shOnly,
			Partitions: []domain.InstrumentMasterPartition{
				{Key:"sh", ExchangeMIC:"XSHG", Complete:true, Observations:shOnly},
				{Key:"bj", ExchangeMIC:"XBSE", Complete:false, Error:"temporary BJ failure"},
			},
		}
	}
	if result, err := ApplyInstrumentMasterSnapshot(ctx, db, partitioned(day1.AddDate(0,0,1))); err != nil || result.Closed != 0 || result.PendingClose != 1 {
		t.Fatalf("first partitioned result=%#v err=%v", result, err)
	}
	if result, err := ApplyInstrumentMasterSnapshot(ctx, db, partitioned(day1.AddDate(0,0,2))); err != nil || result.Closed != 1 {
		t.Fatalf("second partitioned result=%#v err=%v", result, err)
	}
	var bjOpen int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM ref.instrument_identifier WHERE provider='tdx' AND identifier_value='bj920001' AND valid_to IS NULL`).Scan(&bjOpen); err != nil { t.Fatal(err) }
	if bjOpen != 1 { t.Fatalf("BJ open=%d, want frozen/open after failed partition", bjOpen) }
}

func TestApplyInstrumentMasterSnapshotRejectsLargeTruncationPerPartition(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "truncated.duckdb"))
	if err != nil { t.Fatal(err) }
	defer db.Close()
	makeObservations := func(n int) []domain.InstrumentObservation {
		out := make([]domain.InstrumentObservation, 0, n)
		for i := 0; i < n; i++ { out = append(out, snapshotObservation(fmt.Sprintf("sh%06d", 600000+i), fmt.Sprintf("S%d", i))) }
		return out
	}
	day1 := time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC)
	if _, err := ApplyInstrumentMasterSnapshot(ctx, db, domain.InstrumentMasterSnapshot{Source:"tdx", AsOfDate:day1, Complete:true, Observations:makeObservations(100)}); err != nil { t.Fatal(err) }
	current := makeObservations(50)
	if _, err := ApplyInstrumentMasterSnapshot(ctx, db, domain.InstrumentMasterSnapshot{
		Source:"tdx", AsOfDate:day1.AddDate(0,0,1), Complete:true, Observations:current,
		Partitions: []domain.InstrumentMasterPartition{{Key:"sh", ExchangeMIC:"XSHG", Complete:true, Observations:current}},
	}); err == nil { t.Fatal("expected suspicious truncation error") }
	var open int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM ref.instrument_identifier WHERE provider='tdx' AND valid_to IS NULL`).Scan(&open); err != nil { t.Fatal(err) }
	if open != 100 { t.Fatalf("open identifiers=%d, want rollback to 100", open) }
}
