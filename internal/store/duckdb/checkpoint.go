package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
)

// GetCheckpoint returns one durable source/dataset checkpoint.
func GetCheckpoint(ctx context.Context, db *sql.DB, source, dataset, key string) (string, bool, error) {
	if db == nil {
		return "", false, errors.New("duckdb is nil")
	}
	if strings.TrimSpace(source) == "" || strings.TrimSpace(dataset) == "" || strings.TrimSpace(key) == "" {
		return "", false, errors.New("checkpoint source, dataset, and key are required")
	}
	var value string
	err := db.QueryRowContext(ctx, `
		SELECT checkpoint_value
		FROM meta.checkpoint
		WHERE source=? AND dataset=? AND checkpoint_key=?
	`, source, dataset, key).Scan(&value)
	if errors.Is(err, sql.ErrNoRows) {
		return "", false, nil
	}
	if err != nil {
		return "", false, fmt.Errorf("get checkpoint %s/%s/%s: %w", source, dataset, key, err)
	}
	return value, true, nil
}

// SetCheckpoint upserts one durable source/dataset checkpoint.
func SetCheckpoint(ctx context.Context, db *sql.DB, source, dataset, key, value string) error {
	if db == nil {
		return errors.New("duckdb is nil")
	}
	if strings.TrimSpace(source) == "" || strings.TrimSpace(dataset) == "" || strings.TrimSpace(key) == "" {
		return errors.New("checkpoint source, dataset, and key are required")
	}
	if strings.TrimSpace(value) == "" {
		return errors.New("checkpoint value is required")
	}
	if _, err := db.ExecContext(ctx, `
		INSERT INTO meta.checkpoint(source, dataset, checkpoint_key, checkpoint_value)
		VALUES (?, ?, ?, ?)
		ON CONFLICT(source, dataset, checkpoint_key) DO UPDATE SET
			checkpoint_value=excluded.checkpoint_value,
			updated_at=now()
	`, source, dataset, key, value); err != nil {
		return fmt.Errorf("set checkpoint %s/%s/%s: %w", source, dataset, key, err)
	}
	return nil
}

// DeleteCheckpoint clears one durable source/dataset checkpoint. Missing rows
// are treated as success.
func DeleteCheckpoint(ctx context.Context, db *sql.DB, source, dataset, key string) error {
	if db == nil {
		return errors.New("duckdb is nil")
	}
	if strings.TrimSpace(source) == "" || strings.TrimSpace(dataset) == "" || strings.TrimSpace(key) == "" {
		return errors.New("checkpoint source, dataset, and key are required")
	}
	if _, err := db.ExecContext(ctx, `
		DELETE FROM meta.checkpoint
		WHERE source=? AND dataset=? AND checkpoint_key=?
	`, source, dataset, key); err != nil {
		return fmt.Errorf("delete checkpoint %s/%s/%s: %w", source, dataset, key, err)
	}
	return nil
}
