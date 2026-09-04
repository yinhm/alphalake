package duckdb

import (
	"context"
	"errors"
	"path/filepath"
	"testing"
)

func TestReadOperationalStatusReportsDatabaseState(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "status.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	if err := SetCheckpoint(ctx, db, "tdx", "daily_ohlcv", "retry:sh600519", "2026-09-03"); err != nil {
		t.Fatal(err)
	}
	runID, err := StartIngestRun(ctx, db, "tdx", "daily_ohlcv", nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := FinishIngestRun(ctx, db, runID, IngestRunCompleted, nil, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := db.ExecContext(ctx, `
		INSERT INTO meta.validation_result (
			source, dataset, rule_code, severity, passed, details
		) VALUES ('tdx', 'daily_ohlcv', 'test.rule', 'error', false, 'test')
	`); err != nil {
		t.Fatal(err)
	}

	status, err := ReadOperationalStatus(ctx, db, 5)
	if err != nil {
		t.Fatal(err)
	}
	if status.SchemaVersion != status.LatestSchemaVersion || status.SchemaVersion != 7 {
		t.Fatalf("schema versions = %d/%d, want 7/7", status.SchemaVersion, status.LatestSchemaVersion)
	}
	if status.ValidationFailures != 1 || status.Checkpoints != 1 {
		t.Fatalf("validation/checkpoints = %d/%d, want 1/1", status.ValidationFailures, status.Checkpoints)
	}
	if len(status.RecentRuns) != 1 || status.RecentRuns[0].RunID != runID || status.RecentRuns[0].Status != IngestRunCompleted {
		t.Fatalf("recent runs = %#v", status.RecentRuns)
	}
	if status.RecentRuns[0].FinishedAt == nil {
		t.Fatal("expected finished timestamp")
	}
}

func TestReadOperationalStatusDoesNotRequireMigration(t *testing.T) {
	ctx := context.Background()
	db, err := Open(ctx, filepath.Join(t.TempDir(), "empty.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	status, err := ReadOperationalStatus(ctx, db, 5)
	if err != nil {
		t.Fatal(err)
	}
	if status.SchemaVersion != 0 || status.LatestSchemaVersion != 7 {
		t.Fatalf("schema versions = %d/%d, want 0/7", status.SchemaVersion, status.LatestSchemaVersion)
	}
	if len(status.RecentRuns) != 0 {
		t.Fatalf("recent runs = %#v, want none", status.RecentRuns)
	}

	if _, err := ReadOperationalStatus(ctx, db, -1); !errors.Is(err, errors.New("recent run limit must be non-negative")) && err == nil {
		t.Fatal("expected negative limit error")
	}
}
