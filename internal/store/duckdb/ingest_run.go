package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
)

const (
	IngestRunRunning   = "running"
	IngestRunCompleted = "completed"
	IngestRunPartial   = "partial"
	IngestRunFailed    = "failed"
	IngestRunCanceled  = "canceled"
)

// StartIngestRun creates a durable record before acquisition begins. The
// returned ID should be attached to validation/artifact records produced by the
// same logical synchronization attempt.
func StartIngestRun(ctx context.Context, db *sql.DB, source, dataset string, checkpointBefore *string) (int64, error) {
	if db == nil {
		return 0, errors.New("duckdb is nil")
	}
	if strings.TrimSpace(source) == "" || strings.TrimSpace(dataset) == "" {
		return 0, errors.New("ingest source and dataset are required")
	}

	var before any
	if checkpointBefore != nil {
		before = *checkpointBefore
	}

	var id int64
	if err := db.QueryRowContext(ctx, `
		INSERT INTO meta.ingest_run (
			source, dataset, status, checkpoint_before
		) VALUES (?, ?, ?, ?)
		RETURNING ingest_run_id
	`, source, dataset, IngestRunRunning, before).Scan(&id); err != nil {
		return 0, fmt.Errorf("start ingest run: %w", err)
	}
	return id, nil
}

// FinishIngestRun marks an existing run terminal. Terminal status is explicit
// so partial all-market updates are distinguishable from complete success.
func FinishIngestRun(ctx context.Context, db *sql.DB, ingestRunID int64, status string, checkpointAfter *string, runErr error) error {
	if db == nil {
		return errors.New("duckdb is nil")
	}
	if ingestRunID <= 0 {
		return errors.New("ingest run ID must be positive")
	}
	if !terminalIngestStatus(status) {
		return fmt.Errorf("invalid terminal ingest status %q", status)
	}

	var after any
	if checkpointAfter != nil {
		after = *checkpointAfter
	}
	var errorMessage any
	if runErr != nil {
		errorMessage = runErr.Error()
	}

	result, err := db.ExecContext(ctx, `
		UPDATE meta.ingest_run
		SET finished_at = now(),
		    status = ?,
		    checkpoint_after = ?,
		    error_message = ?
		WHERE ingest_run_id = ? AND status = ?
	`, status, after, errorMessage, ingestRunID, IngestRunRunning)
	if err != nil {
		return fmt.Errorf("finish ingest run %d: %w", ingestRunID, err)
	}
	rows, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("read ingest run update result: %w", err)
	}
	if rows != 1 {
		return fmt.Errorf("ingest run %d is missing or already terminal", ingestRunID)
	}
	return nil
}

func terminalIngestStatus(status string) bool {
	switch status {
	case IngestRunCompleted, IngestRunPartial, IngestRunFailed, IngestRunCanceled:
		return true
	default:
		return false
	}
}
