package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"
)

const (
	ProviderResolutionResolved     = "resolved"
	ProviderResolutionPending      = "pending"
	ProviderResolutionAcknowledged = "acknowledged"
)

type ProviderFinancialResolutionInput struct {
	ArtifactID      int64
	Source          string
	SourceFile      string
	ReportPeriod    time.Time
	ProviderCode    string
	MarketMarker    byte
	InstrumentID    int64
	IdentifierValue string
	Reason          string
}

type ProviderFinancialResolutionApplyResult struct {
	Resolved     int
	Pending      int
	Acknowledged int
}

type ProviderFinancialResolutionRow struct {
	ArtifactID         int64
	Source             string
	SourceFile         string
	ReportPeriod       time.Time
	ProviderCode       string
	MarketMarker       byte
	Status             string
	InstrumentID       *int64
	IdentifierValue    string
	Reason             string
	AcknowledgedReason string
	AcknowledgedAt     *time.Time
	LastIngestRunID    *int64
	UpdatedAt          time.Time
}

// ApplyProviderFinancialResolutions records one package revision's raw-record
// identity state. A newly resolved record always becomes resolved. A still-
// unresolved record preserves an explicit operator acknowledgement instead of
// silently reverting it to pending on replay. While acknowledged, the machine
// reason that was reviewed is also preserved; a later resolution still wins.
func ApplyProviderFinancialResolutions(ctx context.Context, db *sql.DB, ingestRunID int64, inputs []ProviderFinancialResolutionInput) (ProviderFinancialResolutionApplyResult, error) {
	var result ProviderFinancialResolutionApplyResult
	if db == nil {
		return result, errors.New("duckdb is nil")
	}
	if ingestRunID <= 0 {
		return result, errors.New("ingest run ID must be positive")
	}
	if len(inputs) == 0 {
		return result, nil
	}
	artifactID := inputs[0].ArtifactID
	if artifactID <= 0 {
		return result, errors.New("artifact ID must be positive")
	}

	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return result, fmt.Errorf("begin provider resolution update: %w", err)
	}
	defer tx.Rollback()
	stmt, err := tx.PrepareContext(ctx, `
		INSERT INTO fundamental.provider_record_resolution (
			artifact_id, source, source_file, report_period, provider_code,
			market_marker, status, instrument_id, identifier_value, reason,
			last_ingest_run_id
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT (artifact_id, provider_code) DO UPDATE SET
			source=excluded.source,
			source_file=excluded.source_file,
			report_period=excluded.report_period,
			market_marker=excluded.market_marker,
			instrument_id=excluded.instrument_id,
			identifier_value=excluded.identifier_value,
			reason=CASE
				WHEN excluded.status='resolved' THEN NULL
				WHEN status='acknowledged' THEN reason
				ELSE excluded.reason
			END,
			status=CASE
				WHEN excluded.status='resolved' THEN 'resolved'
				WHEN status='acknowledged' THEN 'acknowledged'
				ELSE 'pending'
			END,
			last_ingest_run_id=excluded.last_ingest_run_id,
			updated_at=now()
	`)
	if err != nil {
		return result, fmt.Errorf("prepare provider resolution upsert: %w", err)
	}
	defer stmt.Close()

	for i, input := range inputs {
		if input.ArtifactID != artifactID {
			return result, fmt.Errorf("provider resolution input %d mixes artifact %d with %d", i, input.ArtifactID, artifactID)
		}
		input.Source = strings.TrimSpace(input.Source)
		input.SourceFile = strings.TrimSpace(input.SourceFile)
		input.ProviderCode = strings.TrimSpace(input.ProviderCode)
		input.IdentifierValue = strings.TrimSpace(input.IdentifierValue)
		input.Reason = strings.TrimSpace(input.Reason)
		if input.Source == "" || input.SourceFile == "" || input.ProviderCode == "" || input.ReportPeriod.IsZero() {
			return result, fmt.Errorf("provider resolution input %d has incomplete source identity", i)
		}
		status := ProviderResolutionPending
		var instrument any
		var identifier any
		if input.InstrumentID > 0 {
			status = ProviderResolutionResolved
			instrument = input.InstrumentID
			identifier = nullableString(input.IdentifierValue)
			input.Reason = ""
		} else if input.Reason == "" {
			input.Reason = "no unique temporal provider identifier"
		}
		if _, err := stmt.ExecContext(ctx,
			input.ArtifactID, input.Source, input.SourceFile, dateUTC(input.ReportPeriod), input.ProviderCode,
			uint16(input.MarketMarker), status, instrument, identifier, nullableString(input.Reason), ingestRunID,
		); err != nil {
			return result, fmt.Errorf("upsert provider resolution code=%s: %w", input.ProviderCode, err)
		}
	}
	if err := stmt.Close(); err != nil {
		return result, fmt.Errorf("close provider resolution statement: %w", err)
	}

	rows, err := tx.QueryContext(ctx, `
		SELECT status, count(*)
		FROM fundamental.provider_record_resolution
		WHERE artifact_id=?
		GROUP BY status
	`, artifactID)
	if err != nil {
		return result, fmt.Errorf("summarize provider resolutions: %w", err)
	}
	for rows.Next() {
		var status string
		var count int
		if err := rows.Scan(&status, &count); err != nil {
			rows.Close()
			return result, fmt.Errorf("scan provider resolution summary: %w", err)
		}
		switch status {
		case ProviderResolutionResolved:
			result.Resolved = count
		case ProviderResolutionPending:
			result.Pending = count
		case ProviderResolutionAcknowledged:
			result.Acknowledged = count
		default:
			rows.Close()
			return result, fmt.Errorf("unknown provider resolution status %q", status)
		}
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return result, fmt.Errorf("iterate provider resolution summary: %w", err)
	}
	rows.Close()
	if err := tx.Commit(); err != nil {
		return result, fmt.Errorf("commit provider resolutions: %w", err)
	}
	return result, nil
}

func AcknowledgeProviderFinancialResolution(ctx context.Context, db *sql.DB, artifactID int64, providerCode, reason string) (bool, error) {
	if db == nil {
		return false, errors.New("duckdb is nil")
	}
	providerCode = strings.TrimSpace(providerCode)
	reason = strings.TrimSpace(reason)
	if artifactID <= 0 || providerCode == "" || reason == "" {
		return false, errors.New("artifact ID, provider code, and reason are required")
	}
	var status string
	err := db.QueryRowContext(ctx, `
		UPDATE fundamental.provider_record_resolution
		SET status='acknowledged', acknowledged_reason=?, acknowledged_at=now(), updated_at=now()
		WHERE artifact_id=? AND provider_code=? AND status='pending'
		RETURNING status
	`, reason, artifactID, providerCode).Scan(&status)
	if err == nil {
		return true, nil
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return false, fmt.Errorf("acknowledge provider resolution: %w", err)
	}
	if err := db.QueryRowContext(ctx, `
		SELECT status FROM fundamental.provider_record_resolution
		WHERE artifact_id=? AND provider_code=?
	`, artifactID, providerCode).Scan(&status); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return false, fmt.Errorf("provider resolution artifact=%d code=%s not found", artifactID, providerCode)
		}
		return false, fmt.Errorf("query provider resolution status: %w", err)
	}
	if status == ProviderResolutionAcknowledged {
		return false, nil
	}
	return false, fmt.Errorf("provider resolution artifact=%d code=%s has status %s, not pending", artifactID, providerCode, status)
}

// UnacknowledgeProviderFinancialResolution reverses an operator acknowledgement
// back to pending without fabricating a resolution. The previously reviewed
// machine reason is retained; acknowledgement metadata is cleared. The next
// financial replay may refresh the pending reason or resolve the record.
func UnacknowledgeProviderFinancialResolution(ctx context.Context, db *sql.DB, artifactID int64, providerCode string) (bool, error) {
	if db == nil {
		return false, errors.New("duckdb is nil")
	}
	providerCode = strings.TrimSpace(providerCode)
	if artifactID <= 0 || providerCode == "" {
		return false, errors.New("artifact ID and provider code are required")
	}
	var status string
	err := db.QueryRowContext(ctx, `
		UPDATE fundamental.provider_record_resolution
		SET status='pending', acknowledged_reason=NULL, acknowledged_at=NULL, updated_at=now()
		WHERE artifact_id=? AND provider_code=? AND status='acknowledged'
		RETURNING status
	`, artifactID, providerCode).Scan(&status)
	if err == nil {
		return true, nil
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return false, fmt.Errorf("unacknowledge provider resolution: %w", err)
	}
	if err := db.QueryRowContext(ctx, `
		SELECT status FROM fundamental.provider_record_resolution
		WHERE artifact_id=? AND provider_code=?
	`, artifactID, providerCode).Scan(&status); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return false, fmt.Errorf("provider resolution artifact=%d code=%s not found", artifactID, providerCode)
		}
		return false, fmt.Errorf("query provider resolution status: %w", err)
	}
	if status == ProviderResolutionPending {
		return false, nil
	}
	return false, fmt.Errorf("provider resolution artifact=%d code=%s has status %s, not acknowledged", artifactID, providerCode, status)
}

func ListProviderFinancialResolutions(ctx context.Context, db *sql.DB, status string, limit int) ([]ProviderFinancialResolutionRow, error) {
	return ListProviderFinancialResolutionsPage(ctx, db, status, limit, 0)
}

func ListProviderFinancialResolutionsPage(ctx context.Context, db *sql.DB, status string, limit, offset int) ([]ProviderFinancialResolutionRow, error) {
	if db == nil {
		return nil, errors.New("duckdb is nil")
	}
	status = strings.TrimSpace(status)
	if status != "" && status != ProviderResolutionPending && status != ProviderResolutionAcknowledged && status != ProviderResolutionResolved {
		return nil, fmt.Errorf("unsupported provider resolution status %q", status)
	}
	if limit <= 0 {
		limit = 100
	}
	if offset < 0 {
		return nil, errors.New("provider resolution offset must be non-negative")
	}
	query := `
		SELECT artifact_id, source, source_file, report_period, provider_code,
		       market_marker, status, instrument_id, COALESCE(identifier_value,''),
		       COALESCE(reason,''), COALESCE(acknowledged_reason,''), acknowledged_at,
		       last_ingest_run_id, updated_at
		FROM fundamental.provider_record_resolution
	`
	args := []any{}
	if status != "" {
		query += ` WHERE status=?`
		args = append(args, status)
	}
	query += ` ORDER BY report_period, artifact_id, provider_code LIMIT ? OFFSET ?`
	args = append(args, limit, offset)
	rows, err := db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, fmt.Errorf("query provider resolutions: %w", err)
	}
	defer rows.Close()
	var out []ProviderFinancialResolutionRow
	for rows.Next() {
		var row ProviderFinancialResolutionRow
		var marker uint16
		var instrument sql.NullInt64
		var acknowledged sql.NullTime
		var runID sql.NullInt64
		if err := rows.Scan(
			&row.ArtifactID, &row.Source, &row.SourceFile, &row.ReportPeriod, &row.ProviderCode,
			&marker, &row.Status, &instrument, &row.IdentifierValue, &row.Reason,
			&row.AcknowledgedReason, &acknowledged, &runID, &row.UpdatedAt,
		); err != nil {
			return nil, fmt.Errorf("scan provider resolution: %w", err)
		}
		row.MarketMarker = byte(marker)
		if instrument.Valid {
			v := instrument.Int64
			row.InstrumentID = &v
		}
		if acknowledged.Valid {
			v := acknowledged.Time
			row.AcknowledgedAt = &v
		}
		if runID.Valid {
			v := runID.Int64
			row.LastIngestRunID = &v
		}
		out = append(out, row)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate provider resolutions: %w", err)
	}
	return out, nil
}
