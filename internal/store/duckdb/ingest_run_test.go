package duckdb

import (
	"context"
	"errors"
	"path/filepath"
	"testing"
)

func TestIngestRunLifecyclePersistsTerminalState(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "run.duckdb"))
	if err != nil {
		t.Fatalf("OpenAndMigrate() error = %v", err)
	}
	defer db.Close()

	before := "per-instrument"
	id, err := StartIngestRun(ctx, db, "tdx", "daily_ohlcv", &before)
	if err != nil {
		t.Fatalf("StartIngestRun() error = %v", err)
	}
	if id <= 0 {
		t.Fatalf("ingest run ID = %d, want positive", id)
	}

	runErr := errors.New("one symbol failed")
	if err := FinishIngestRun(ctx, db, id, IngestRunPartial, nil, runErr); err != nil {
		t.Fatalf("FinishIngestRun() error = %v", err)
	}

	var status, checkpointBefore, errorMessage string
	var finished bool
	if err := db.QueryRowContext(ctx, `
		SELECT status, checkpoint_before, error_message, finished_at IS NOT NULL
		FROM meta.ingest_run WHERE ingest_run_id = ?
	`, id).Scan(&status, &checkpointBefore, &errorMessage, &finished); err != nil {
		t.Fatalf("query ingest run: %v", err)
	}
	if status != IngestRunPartial || checkpointBefore != before || errorMessage != runErr.Error() || !finished {
		t.Fatalf("run = status:%q checkpoint:%q error:%q finished:%v", status, checkpointBefore, errorMessage, finished)
	}

	if err := FinishIngestRun(ctx, db, id, IngestRunCompleted, nil, nil); err == nil {
		t.Fatal("second FinishIngestRun() expected terminal-state error")
	}
}

func TestFinishIngestRunRejectsRunningStatus(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "run.duckdb"))
	if err != nil {
		t.Fatalf("OpenAndMigrate() error = %v", err)
	}
	defer db.Close()

	id, err := StartIngestRun(ctx, db, "tdx", "daily_ohlcv", nil)
	if err != nil {
		t.Fatalf("StartIngestRun() error = %v", err)
	}
	if err := FinishIngestRun(ctx, db, id, IngestRunRunning, nil, nil); err == nil {
		t.Fatal("FinishIngestRun() expected invalid status error")
	}
}
