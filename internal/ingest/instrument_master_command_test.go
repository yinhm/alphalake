package ingest

import (
	"context"
	"github.com/yinhm/alphalake/internal/domain"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
	"path/filepath"
	"testing"
	"time"
)

func TestSyncInstrumentMasterDoesNotFetchDaily(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenInitialized(ctx, filepath.Join(t.TempDir(), "master.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	source := &fakeIncrementalTDXSource{observations: []domain.InstrumentObservation{observation(domain.InstrumentEquity, "XSHG", "样本", "sh600001")}}
	for i := 0; i < 2; i++ {
		r, e := SyncTDXInstrumentMaster(ctx, db, source)
		if e != nil || len(r.Observations) != 1 {
			t.Fatalf("%+v %v", r, e)
		}
	}
	if len(source.fullCalls) != 0 || len(source.sinceCalls) != 0 {
		t.Fatal("unexpected daily request")
	}
	var n int
	if err = db.QueryRowContext(ctx, `SELECT count(*) FROM meta.ingest_run WHERE dataset='instrument_master' AND status='completed'`).Scan(&n); err != nil || n != 2 {
		t.Fatalf("runs %d %v", n, err)
	}
	if err = db.QueryRowContext(ctx, `SELECT count(*) FROM core.instrument`).Scan(&n); err != nil || n != 1 {
		t.Fatalf("instruments %d %v", n, err)
	}
}

func TestSyncInstrumentMasterRetainsPartialFailure(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "failure.duckdb")
	db, err := duckstore.OpenInitialized(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	sh := observation(domain.InstrumentEquity, "XSHG", "样本", "sh600001")
	source := &partitionFailureDailySource{snapshot: domain.InstrumentMasterSnapshot{Source: "tdx", AsOfDate: time.Date(2026, 9, 9, 0, 0, 0, 0, time.UTC), Observations: []domain.InstrumentObservation{sh}, Partitions: []domain.InstrumentMasterPartition{
		{Key: "sh", ExchangeMIC: "XSHG", Complete: true, Observations: []domain.InstrumentObservation{sh}},
		{Key: "bj", ExchangeMIC: "XBSE", Error: "three nodes failed", Complete: false},
	}}}
	result, err := SyncTDXInstrumentMaster(ctx, db, source)
	if err == nil || len(result.Failures) != 1 {
		t.Fatalf("%+v %v", result, err)
	}
	if err = db.Close(); err != nil {
		t.Fatal(err)
	}
	db, err = duckstore.Open(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	var n int
	if err = db.QueryRowContext(ctx, `SELECT count(*) FROM meta.validation_result WHERE dataset='instrument_master' AND subject_key='bj'`).Scan(&n); err != nil || n != 1 {
		t.Fatalf("diagnostics %d %v", n, err)
	}
	var status string
	if err = db.QueryRowContext(ctx, `SELECT status FROM meta.ingest_run WHERE dataset='instrument_master'`).Scan(&status); err != nil || status != "partial" {
		t.Fatalf("status %s %v", status, err)
	}
}
