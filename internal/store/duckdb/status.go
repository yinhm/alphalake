package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"
)

type RecentIngestRun struct {
	RunID      int64
	Source     string
	Dataset    string
	Status     string
	StartedAt  time.Time
	FinishedAt *time.Time
}

type OperationalStatus struct {
	SchemaVersion       int
	LatestSchemaVersion int
	ValidationFailures  int
	Checkpoints         int
	RecentRuns          []RecentIngestRun
}

// ReadOperationalStatus inspects an existing AlphaLake database without
// applying migrations. This lets the CLI report pending schema upgrades instead
// of silently mutating the database as a side effect of a status command.
func ReadOperationalStatus(ctx context.Context, db *sql.DB, recentLimit int) (OperationalStatus, error) {
	var out OperationalStatus
	if db == nil {
		return out, errors.New("duckdb is nil")
	}
	if recentLimit < 0 {
		return out, errors.New("recent run limit must be non-negative")
	}
	migrations, err := Migrations()
	if err != nil {
		return out, err
	}
	out.LatestSchemaVersion = len(migrations)
	out.SchemaVersion, err = CurrentSchemaVersion(ctx, db)
	if err != nil {
		return out, err
	}
	if out.SchemaVersion == 0 {
		return out, nil
	}

	if err := db.QueryRowContext(ctx, `
		SELECT count(*) FROM meta.validation_result WHERE passed=false
	`).Scan(&out.ValidationFailures); err != nil {
		return out, fmt.Errorf("count validation failures: %w", err)
	}
	if err := db.QueryRowContext(ctx, `
		SELECT count(*) FROM meta.checkpoint
	`).Scan(&out.Checkpoints); err != nil {
		return out, fmt.Errorf("count checkpoints: %w", err)
	}
	if recentLimit == 0 {
		return out, nil
	}

	rows, err := db.QueryContext(ctx, `
		SELECT ingest_run_id, source, dataset, status, started_at, finished_at
		FROM meta.ingest_run
		ORDER BY started_at DESC, ingest_run_id DESC
		LIMIT ?
	`, recentLimit)
	if err != nil {
		return out, fmt.Errorf("query recent ingest runs: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var run RecentIngestRun
		var finished sql.NullTime
		if err := rows.Scan(&run.RunID, &run.Source, &run.Dataset, &run.Status, &run.StartedAt, &finished); err != nil {
			return out, fmt.Errorf("scan recent ingest run: %w", err)
		}
		if finished.Valid {
			t := finished.Time
			run.FinishedAt = &t
		}
		out.RecentRuns = append(out.RecentRuns, run)
	}
	if err := rows.Err(); err != nil {
		return out, fmt.Errorf("iterate recent ingest runs: %w", err)
	}
	return out, nil
}
