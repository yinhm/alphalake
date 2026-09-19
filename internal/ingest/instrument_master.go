package ingest

import (
	"context"
	"database/sql"
	"fmt"

	"github.com/yinhm/alphalake/internal/domain"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

type instrumentSnapshotSource interface {
	InstrumentSnapshot(context.Context) (domain.InstrumentMasterSnapshot, error)
}

type InstrumentMasterFailure struct {
	Partition string
	Err       error
}

type InstrumentMasterRefreshResult struct {
	Observations  []domain.InstrumentObservation
	InstrumentIDs []int64
	Failures      []InstrumentMasterFailure
}

// refreshInstrumentMaster requires a partitioned provider snapshot; only
// observations from successfully-applied partitions are returned to the caller.
// Partition failures are durable, queryable run diagnostics and also returned
// to the workflow so a healthy partial refresh cannot masquerade as completed.
func refreshInstrumentMaster(ctx context.Context, db *sql.DB, ingestRunID int64, source instrumentSnapshotSource) (InstrumentMasterRefreshResult, error) {
	var refresh InstrumentMasterRefreshResult
	snapshot, err := source.InstrumentSnapshot(ctx)
	if err != nil {
		return refresh, fmt.Errorf("load instrument master snapshot: %w", err)
	}
	result, err := duckstore.ApplyInstrumentMasterSnapshot(ctx, db, snapshot)
	if err != nil {
		return refresh, fmt.Errorf("apply instrument master snapshot: %w", err)
	}
	refresh.Observations = make([]domain.InstrumentObservation, 0, len(snapshot.Observations))
	refresh.InstrumentIDs = make([]int64, 0, len(snapshot.Observations))
	for i, id := range result.InstrumentIDs {
		if id <= 0 {
			continue
		}
		refresh.Observations = append(refresh.Observations, snapshot.Observations[i])
		refresh.InstrumentIDs = append(refresh.InstrumentIDs, id)
	}
	if len(refresh.Observations) == 0 {
		return refresh, fmt.Errorf("instrument master had no successfully applied partitions")
	}
	if len(result.PartitionFailures) != 0 {
		diagnostics := make([]duckstore.IngestDiagnostic, 0, len(result.PartitionFailures))
		refresh.Failures = make([]InstrumentMasterFailure, 0, len(result.PartitionFailures))
		for _, failure := range result.PartitionFailures {
			refresh.Failures = append(refresh.Failures, InstrumentMasterFailure{Partition: failure.Partition, Err: failure.Err})
			diagnostics = append(diagnostics, duckstore.IngestDiagnostic{
				RuleCode:    "instrument_master.partition_failure",
				Severity:    "warning",
				SubjectType: "exchange_partition",
				SubjectKey:  failure.Partition,
				Details:     failure.Err.Error(),
			})
		}
		if err := duckstore.RecordIngestDiagnostics(ctx, db, ingestRunID, snapshot.Source, "instrument_master", diagnostics); err != nil {
			return refresh, fmt.Errorf("record instrument master diagnostics: %w", err)
		}
	}
	return refresh, nil
}

// SyncTDXInstrumentMaster 复用分区刷新，不隐式下载日线。
func SyncTDXInstrumentMaster(ctx context.Context, db *sql.DB, source instrumentSnapshotSource) (result InstrumentMasterRefreshResult, retErr error) {
	run, err := duckstore.StartIngestRun(ctx, db, "tdx", "instrument_master", nil)
	if err != nil {
		return result, err
	}
	defer func() {
		status := duckstore.IngestRunCompleted
		if retErr != nil {
			status = duckstore.IngestRunFailed
		}
		if len(result.Failures) > 0 && len(result.Observations) > 0 {
			status = duckstore.IngestRunPartial
		}
		if ctx.Err() != nil {
			status = duckstore.IngestRunCanceled
		}
		finalizeTrackedRun(ctx, db, run, status, &retErr)
	}()
	result, retErr = refreshInstrumentMaster(ctx, db, run, source)
	if retErr == nil && len(result.Failures) > 0 {
		retErr = fmt.Errorf("instrument master incomplete: %d partition failures; first %s: %w", len(result.Failures), result.Failures[0].Partition, result.Failures[0].Err)
	}
	return result, retErr
}
