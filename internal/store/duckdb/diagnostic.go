package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
)

type IngestDiagnostic struct {
	RuleCode    string
	Severity    string
	SubjectType string
	SubjectKey  string
	Details     string
}

// RecordIngestDiagnostics persists acquisition/workflow diagnostics that are
// not row-level canonical validation. This is intentionally separate from the
// atomic daily-bar validation path: a partition outage is run metadata, not a
// property of one market-data observation.
func RecordIngestDiagnostics(ctx context.Context, db *sql.DB, ingestRunID int64, source, dataset string, diagnostics []IngestDiagnostic) error {
	if db == nil {
		return errors.New("duckdb is nil")
	}
	if ingestRunID <= 0 {
		return errors.New("ingest run ID must be positive")
	}
	source = strings.TrimSpace(source)
	dataset = strings.TrimSpace(dataset)
	if source == "" || dataset == "" {
		return errors.New("diagnostic source and dataset are required")
	}
	if len(diagnostics) == 0 {
		return nil
	}

	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("begin ingest diagnostic write: %w", err)
	}
	defer tx.Rollback()
	stmt, err := tx.PrepareContext(ctx, `
		INSERT INTO meta.validation_result (
			ingest_run_id, source, dataset, rule_code, severity,
			subject_type, subject_key, passed, details
		) VALUES (?, ?, ?, ?, ?, ?, ?, false, ?)
	`)
	if err != nil {
		return fmt.Errorf("prepare ingest diagnostic write: %w", err)
	}
	defer stmt.Close()
	for i, diagnostic := range diagnostics {
		rule := strings.TrimSpace(diagnostic.RuleCode)
		severity := strings.TrimSpace(diagnostic.Severity)
		if rule == "" || severity == "" {
			return fmt.Errorf("diagnostic %d rule code and severity are required", i)
		}
		if _, err := stmt.ExecContext(ctx,
			ingestRunID,
			source,
			dataset,
			rule,
			severity,
			nullableString(diagnostic.SubjectType),
			nullableString(diagnostic.SubjectKey),
			nullableString(diagnostic.Details),
		); err != nil {
			return fmt.Errorf("insert ingest diagnostic %s/%s: %w", rule, diagnostic.SubjectKey, err)
		}
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("commit ingest diagnostic write: %w", err)
	}
	return nil
}
