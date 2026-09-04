package ingest

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

type partitionFailureDailySource struct {
	snapshot domain.InstrumentMasterSnapshot
}

func (f *partitionFailureDailySource) Instruments(context.Context) ([]domain.InstrumentObservation, error) {
	return f.snapshot.Observations, nil
}

func (f *partitionFailureDailySource) InstrumentSnapshot(context.Context) (domain.InstrumentMasterSnapshot, error) {
	return f.snapshot, nil
}

func (f *partitionFailureDailySource) StockDailyBars(_ context.Context, instrumentID int64, _ string) ([]domain.DailyBar, error) {
	return []domain.DailyBar{{
		InstrumentID: instrumentID,
		TradeDate: time.Date(2026, 9, 4, 0, 0, 0, 0, time.UTC),
		Open: 10, High: 11, Low: 9, Close: 10.5,
		Volume: 1000, Amount: 10500, Source: "tdx",
	}}, nil
}

func (f *partitionFailureDailySource) StockDailyBarsSince(ctx context.Context, instrumentID int64, symbol string, _ time.Time) ([]domain.DailyBar, error) {
	return f.StockDailyBars(ctx, instrumentID, symbol)
}

func TestDailySyncSurfacesMasterPartitionFailureWithoutBlockingHealthyPartition(t *testing.T) {
	ctx := context.Background()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "master-visible.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	sh := observation(domain.InstrumentEquity, "XSHG", "SH-A", "sh600001")
	source := &partitionFailureDailySource{snapshot: domain.InstrumentMasterSnapshot{
		Source: "tdx",
		AsOfDate: time.Date(2026, 9, 4, 0, 0, 0, 0, time.UTC),
		Complete: false,
		Observations: []domain.InstrumentObservation{sh},
		Partitions: []domain.InstrumentMasterPartition{
			{Key: "sh", ExchangeMIC: "XSHG", Complete: true, Observations: []domain.InstrumentObservation{sh}},
			{Key: "bj", ExchangeMIC: "XBSE", Complete: false, Error: "temporary BJ timeout"},
		},
	}}

	summary, err := SyncAllTDXDaily(ctx, db, source)
	if err != nil {
		t.Fatalf("healthy partition should continue, got %v", err)
	}
	if summary.Synced != 1 || len(summary.MasterFailures) != 1 || summary.MasterFailures[0].Partition != "bj" {
		t.Fatalf("summary=%#v", summary)
	}

	var status string
	if err := db.QueryRowContext(ctx, `SELECT status FROM meta.ingest_run WHERE ingest_run_id=?`, summary.RunID).Scan(&status); err != nil {
		t.Fatal(err)
	}
	if status != duckstore.IngestRunPartial {
		t.Fatalf("run status=%q, want partial", status)
	}

	var rule, subject, details string
	if err := db.QueryRowContext(ctx, `
		SELECT rule_code, subject_key, details
		FROM meta.validation_result
		WHERE ingest_run_id=? AND dataset='instrument_master'
	`, summary.RunID).Scan(&rule, &subject, &details); err != nil {
		t.Fatal(err)
	}
	if rule != "instrument_master.partition_failure" || subject != "bj" || details == "" {
		t.Fatalf("diagnostic=%q/%q/%q", rule, subject, details)
	}
}
