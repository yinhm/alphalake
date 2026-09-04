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
// source exposes one. That path atomically upserts current instruments and closes
// identifiers missing from a verified complete snapshot. Narrow fake/legacy
// sources may still expose only Instruments(), in which case no destructive
// lifecycle inference is attempted.
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
		return snapshot.Observations, result.InstrumentIDs, nil
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
