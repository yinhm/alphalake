package ingest

import (
	"context"
	"database/sql"
	"fmt"

	"github.com/yinhm/alphalake/internal/domain"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

type instrumentListSource interface {
	Instruments(context.Context) ([]domain.InstrumentObservation, error)
}

type instrumentSnapshotSource interface {
	InstrumentSnapshot(context.Context) (domain.InstrumentMasterSnapshot, error)
}

// refreshInstrumentMaster prefers a point-in-time provider snapshot when the
// source exposes one. Partitioned snapshots are applied independently; only
// observations from successfully-applied partitions are returned to the caller,
// so one failed exchange cannot poison the rest of the acquisition workflow.
func refreshInstrumentMaster(ctx context.Context, db *sql.DB, source instrumentListSource) ([]domain.InstrumentObservation, []int64, error) {
	if snapshotSource, ok := source.(instrumentSnapshotSource); ok {
		snapshot, err := snapshotSource.InstrumentSnapshot(ctx)
		if err != nil {
			return nil, nil, fmt.Errorf("load instrument master snapshot: %w", err)
		}
		result, err := duckstore.ApplyInstrumentMasterSnapshot(ctx, db, snapshot)
		if err != nil {
			return nil, nil, fmt.Errorf("apply instrument master snapshot: %w", err)
		}
		observations := make([]domain.InstrumentObservation, 0, len(snapshot.Observations))
		ids := make([]int64, 0, len(snapshot.Observations))
		for i, id := range result.InstrumentIDs {
			if id <= 0 {
				continue
			}
			observations = append(observations, snapshot.Observations[i])
			ids = append(ids, id)
		}
		if len(observations) == 0 {
			return nil, nil, fmt.Errorf("instrument master had no successfully applied partitions")
		}
		return observations, ids, nil
	}

	observations, err := source.Instruments(ctx)
	if err != nil {
		return nil, nil, fmt.Errorf("list instruments: %w", err)
	}
	ids, err := duckstore.UpsertInstruments(ctx, db, observations)
	if err != nil {
		return nil, nil, fmt.Errorf("refresh canonical instrument master: %w", err)
	}
	return observations, ids, nil
}
