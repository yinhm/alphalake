package duckdb

import (
	"context"
	"path/filepath"
	"testing"

	"github.com/yinhm/alphalake/internal/validate"
)

func TestRecordValidationViolationsPersistsFailures(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "validation.duckdb"))
	if err != nil {
		t.Fatalf("OpenAndMigrate() error = %v", err)
	}
	defer db.Close()

	violations := []validate.Violation{{
		RuleCode: "daily.high_bound", Severity: "error",
		SubjectKey: "42:2026-09-03", Details: "high below close",
	}}
	if err := RecordValidationViolations(ctx, db, nil, "tdx", "daily_ohlcv", "daily_bar", violations); err != nil {
		t.Fatalf("RecordValidationViolations() error = %v", err)
	}

	var rule, subject string
	var passed bool
	if err := db.QueryRowContext(ctx, `
		SELECT rule_code, subject_key, passed
		FROM meta.validation_result
		WHERE source='tdx' AND dataset='daily_ohlcv'
	`).Scan(&rule, &subject, &passed); err != nil {
		t.Fatalf("query validation result: %v", err)
	}
	if rule != "daily.high_bound" || subject != "42:2026-09-03" || passed {
		t.Fatalf("stored validation = %q/%q/%v", rule, subject, passed)
	}
}
