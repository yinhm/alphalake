package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	"github.com/yinhm/alphalake/internal/validate"
)

// ApplyDailyIngestBatchForRun atomically publishes one instrument's validated
// daily ingest result: canonical good rows, validation evidence, and the durable
// quarantine retry checkpoint. A failure in any component rolls the whole batch
// back, so a bad row can never be forgotten because bars committed while the
// retry checkpoint failed to persist.
func ApplyDailyIngestBatchForRun(
	ctx context.Context,
	db *sql.DB,
	ingestRunID int64,
	source, dataset, subjectType, checkpointKey string,
	validBars []domain.DailyBar,
	violations []validate.Violation,
	retryFrom *time.Time,
) error {
	if db == nil {
		return errors.New("duckdb is nil")
	}
	if ingestRunID <= 0 {
		return errors.New("ingest run ID must be positive")
	}
	source = strings.TrimSpace(source)
	dataset = strings.TrimSpace(dataset)
	checkpointKey = strings.TrimSpace(checkpointKey)
	if source == "" || dataset == "" || checkpointKey == "" {
		return errors.New("source, dataset, and checkpoint key are required")
	}
	if err := validateDailyBarKeys(validBars); err != nil {
		return err
	}
	var retryValue *string
	if retryFrom != nil {
		if retryFrom.IsZero() {
			return errors.New("retry date must not be zero")
		}
		day := dateUTC(*retryFrom).Format("2006-01-02")
		retryValue = &day
	}

	return withDailyWriteTransaction(ctx, db, func(conn *sql.Conn) error {
		if err := mergeDailyBarsOnConn(ctx, conn, validBars, &ingestRunID); err != nil {
			return err
		}
		if err := insertValidationViolationsOnConn(
			ctx, conn, ingestRunID, source, dataset, subjectType, violations,
		); err != nil {
			return err
		}
		if retryValue == nil {
			if _, err := conn.ExecContext(ctx, `
				DELETE FROM meta.checkpoint
				WHERE source=? AND dataset=? AND checkpoint_key=?
			`, source, dataset, checkpointKey); err != nil {
				return fmt.Errorf("clear daily retry checkpoint: %w", err)
			}
			return nil
		}
		if _, err := conn.ExecContext(ctx, `
			INSERT INTO meta.checkpoint(source, dataset, checkpoint_key, checkpoint_value)
			VALUES (?, ?, ?, ?)
			ON CONFLICT(source, dataset, checkpoint_key) DO UPDATE SET
				checkpoint_value=excluded.checkpoint_value,
				updated_at=now()
		`, source, dataset, checkpointKey, *retryValue); err != nil {
			return fmt.Errorf("set daily retry checkpoint: %w", err)
		}
		return nil
	})
}

func insertValidationViolationsOnConn(
	ctx context.Context,
	conn *sql.Conn,
	ingestRunID int64,
	source, dataset, subjectType string,
	violations []validate.Violation,
) error {
	if len(violations) == 0 {
		return nil
	}
	stmt, err := conn.PrepareContext(ctx, `
		INSERT INTO meta.validation_result (
			ingest_run_id, source, dataset, rule_code, severity,
			subject_type, subject_key, passed, details
		) VALUES (?, ?, ?, ?, ?, ?, ?, false, ?)
	`)
	if err != nil {
		return fmt.Errorf("prepare daily validation write: %w", err)
	}
	defer stmt.Close()
	for _, violation := range violations {
		if _, err := stmt.ExecContext(ctx,
			ingestRunID,
			source,
			dataset,
			violation.RuleCode,
			violation.Severity,
			nullableString(subjectType),
			nullableString(violation.SubjectKey),
			violation.Details,
		); err != nil {
			return fmt.Errorf("insert daily validation %s for %s: %w", violation.RuleCode, violation.SubjectKey, err)
		}
	}
	return nil
}
