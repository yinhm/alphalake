package ingest

import (
	"context"
	"database/sql"
	"errors"

	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

// finalizeTrackedRun persists a terminal ingest status even when the acquisition
// context has been canceled. A failure to persist run state is joined with the
// workflow error rather than masking it.
func finalizeTrackedRun(ctx context.Context, db *sql.DB, runID int64, status string, runErr *error) {
	finishCtx := context.WithoutCancel(ctx)
	if err := duckstore.FinishIngestRun(finishCtx, db, runID, status, nil, *runErr); err != nil {
		if *runErr == nil {
			*runErr = err
		} else {
			*runErr = errors.Join(*runErr, err)
		}
	}
}

func equityOrETF(t string) bool {
	return t == "equity" || t == "etf"
}
