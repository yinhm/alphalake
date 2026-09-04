package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"

	"github.com/yinhm/alphalake/internal/validate"
)

// RecordValidationViolations persists failed data-quality rules. Successful
// per-row checks are intentionally not stored in v0 to avoid exploding the
// metadata table; absence of violations plus a successful ingest is the pass signal.
func RecordValidationViolations(
	ctx context.Context,
	db *sql.DB,
	ingestRunID *int64,
	source string,
	dataset string,
	subjectType string,
	violations []validate.Violation,
) error {
	if db == nil {
		return errors.New("duckdb is nil")
	}
	if len(violations) == 0 {
		return nil
	}
	if strings.TrimSpace(source) == "" || strings.TrimSpace(dataset) == "" {
		return errors.New("validation source and dataset are required")
	}

	var runID any
	if ingestRunID != nil {
		runID = *ingestRunID
	}

	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("begin validation write: %w", err)
	}
	defer tx.Rollback()

	stmt, err := tx.PrepareContext(ctx, `
		INSERT INTO meta.validation_result (
			ingest_run_id, source, dataset, rule_code, severity,
			subject_type, subject_key, passed, details
		) VALUES (?, ?, ?, ?, ?, ?, ?, false, ?)
	`)
	if err != nil {
		return fmt.Errorf("prepare validation write: %w", err)
	}
	defer stmt.Close()

	for _, violation := range violations {
		if _, err := stmt.ExecContext(ctx,
			runID,
			source,
			dataset,
			violation.RuleCode,
			violation.Severity,
			nullableString(subjectType),
			nullableString(violation.SubjectKey),
			violation.Details,
		); err != nil {
			return fmt.Errorf("insert validation %s for %s: %w", violation.RuleCode, violation.SubjectKey, err)
		}
	}

	if err := tx.Commit(); err != nil {
		return fmt.Errorf("commit validation write: %w", err)
	}
	return nil
}
