package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

type FilingResolutionRow struct {
	FilingID                  int64
	Source                    string
	SourceFilingID            string
	ProviderCode              string
	ExchangeMIC               string
	SecurityName              string
	Title                     string
	FilingType                domain.FilingType
	FilingVariant             domain.FilingVariant
	ReportPeriod              *time.Time
	AnnouncementDate          *time.Time
	AnnouncementTime          *time.Time
	AnnouncementTimePrecision string
	ResolutionStatus          string
	ResolutionReason          string
	SourceURL                 string
	UpdatedAt                 time.Time
}

func ListFilingResolutionsPage(ctx context.Context, db *sql.DB, status string, limit, offset int) ([]FilingResolutionRow, error) {
	if db == nil {
		return nil, errors.New("duckdb is nil")
	}
	status = strings.TrimSpace(status)
	if status != "" && status != domain.FilingResolutionPending && status != domain.FilingResolutionResolved && status != domain.FilingResolutionAcknowledged {
		return nil, fmt.Errorf("unsupported filing resolution status %q", status)
	}
	if limit <= 0 {
		limit = 100
	}
	if offset < 0 {
		return nil, errors.New("filing resolution offset must be non-negative")
	}
	query := `
		SELECT
			filing_id, source, source_filing_id, provider_code,
			COALESCE(exchange_mic,''), COALESCE(security_name,''), COALESCE(title,''),
			COALESCE(filing_type,'unknown'), filing_variant,
			report_period, announcement_date, announcement_time,
			COALESCE(announcement_time_precision,'timestamp'), resolution_status,
			COALESCE(resolution_reason,''), COALESCE(source_url,''), last_seen_at
		FROM fundamental.filing
	`
	args := []any{}
	if status != "" {
		query += ` WHERE resolution_status=?`
		args = append(args, status)
	}
	query += ` ORDER BY announcement_date NULLS LAST, filing_id LIMIT ? OFFSET ?`
	args = append(args, limit, offset)
	rows, err := db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, fmt.Errorf("query filing resolutions: %w", err)
	}
	defer rows.Close()
	var out []FilingResolutionRow
	for rows.Next() {
		var row FilingResolutionRow
		var filingType, filingVariant string
		var reportPeriod, announcementDate, announcementTime sql.NullTime
		if err := rows.Scan(
			&row.FilingID, &row.Source, &row.SourceFilingID, &row.ProviderCode,
			&row.ExchangeMIC, &row.SecurityName, &row.Title,
			&filingType, &filingVariant,
			&reportPeriod, &announcementDate, &announcementTime,
			&row.AnnouncementTimePrecision, &row.ResolutionStatus,
			&row.ResolutionReason, &row.SourceURL, &row.UpdatedAt,
		); err != nil {
			return nil, fmt.Errorf("scan filing resolution: %w", err)
		}
		row.FilingType = domain.FilingType(filingType)
		row.FilingVariant = domain.FilingVariant(filingVariant)
		if reportPeriod.Valid {
			v := reportPeriod.Time
			row.ReportPeriod = &v
		}
		if announcementDate.Valid {
			v := announcementDate.Time
			row.AnnouncementDate = &v
		}
		if announcementTime.Valid {
			v := announcementTime.Time
			row.AnnouncementTime = &v
		}
		out = append(out, row)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate filing resolutions: %w", err)
	}
	return out, nil
}
