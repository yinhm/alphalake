package duckdb

import (
	"context"
	"fmt"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

func TestApplyInstrumentMasterSnapshotKeepsHealthyPartitionWhenOtherGuardFails(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "partition-isolation.duckdb"))
	if err != nil { t.Fatal(err) }
	defer db.Close()

	makeSH := func(n int) []domain.InstrumentObservation {
		out := make([]domain.InstrumentObservation, 0, n)
		for i := 0; i < n; i++ {
			out = append(out, snapshotObservation(fmt.Sprintf("sh%06d", 600000+i), fmt.Sprintf("SH-%d", i)))
		}
		return out
	}
	day1 := time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC)
	initialSH := makeSH(100)
	initialBJ := []domain.InstrumentObservation{snapshotObservation("bj920001", "BJ-old")}
	initial := append(append([]domain.InstrumentObservation{}, initialSH...), initialBJ...)
	if _, err := ApplyInstrumentMasterSnapshot(ctx, db, domain.InstrumentMasterSnapshot{Source:"tdx", AsOfDate:day1, Complete:true, Observations:initial}); err != nil {
		t.Fatal(err)
	}

	badSH := makeSH(50) // >20% truncation: this partition must roll back.
	goodBJ := []domain.InstrumentObservation{snapshotObservation("bj920001", "BJ-new")}
	flat := append(append([]domain.InstrumentObservation{}, badSH...), goodBJ...)
	result, err := ApplyInstrumentMasterSnapshot(ctx, db, domain.InstrumentMasterSnapshot{
		Source:"tdx", AsOfDate:day1.AddDate(0,0,1), Complete:true, Observations:flat,
		Partitions: []domain.InstrumentMasterPartition{
			{Key:"sh", ExchangeMIC:"XSHG", Complete:true, Observations:badSH},
			{Key:"bj", ExchangeMIC:"XBSE", Complete:true, Observations:goodBJ},
		},
	})
	if err != nil { t.Fatalf("partial partition apply returned fatal error: %v", err) }
	if len(result.PartitionFailures) != 1 || result.PartitionFailures[0].Partition != "sh" {
		t.Fatalf("partition failures=%#v", result.PartitionFailures)
	}
	for i := 0; i < len(badSH); i++ {
		if result.InstrumentIDs[i] != 0 {
			t.Fatalf("failed SH partition produced instrument ID at %d: %d", i, result.InstrumentIDs[i])
		}
	}
	if result.InstrumentIDs[len(flat)-1] <= 0 {
		t.Fatalf("healthy BJ partition did not produce an instrument ID: %#v", result.InstrumentIDs)
	}

	var bjName string
	if err := db.QueryRowContext(ctx, `
		SELECT i.name FROM ref.instrument i
		JOIN ref.instrument_identifier x ON x.instrument_id=i.instrument_id
		WHERE x.provider='tdx' AND x.identifier_value='bj920001' AND x.valid_to IS NULL
	`).Scan(&bjName); err != nil { t.Fatal(err) }
	if bjName != "BJ-new" { t.Fatalf("BJ name=%q, want committed healthy update", bjName) }

	var shOpen int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM ref.instrument_identifier x JOIN ref.instrument i ON i.instrument_id=x.instrument_id WHERE x.provider='tdx' AND x.valid_to IS NULL AND i.exchange_mic='XSHG'`).Scan(&shOpen); err != nil { t.Fatal(err) }
	if shOpen != 100 { t.Fatalf("SH open=%d, want failed partition rollback to 100", shOpen) }
}
