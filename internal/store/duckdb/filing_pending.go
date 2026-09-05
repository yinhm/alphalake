package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"fmt"

	"github.com/yinhm/alphalake/internal/domain"
)

type PendingFilingResolutionResult struct {
	Attempted    int
	Resolved     int
	StillPending int
}

// RefreshPendingFilingResolutions retries retained unresolved filing metadata
// against the current temporal security master. This is intentionally local and
// independent of catalogue checkpoints, so later lifecycle enrichment can repair
// old filing windows without redownloading CNINFO pages or documents.
func RefreshPendingFilingResolutions(ctx context.Context, db *sql.DB, ingestRunID int64, batchSize int) (PendingFilingResolutionResult, error) {
	var result PendingFilingResolutionResult
	if db == nil {
		return result, errors.New("duckdb is nil")
	}
	if ingestRunID <= 0 {
		return result, errors.New("ingest run ID must be positive")
	}
	if batchSize <= 0 {
		batchSize = 500
	}
	var cursor int64
	for {
		filings, nextCursor, err := listPendingFilingsAfter(ctx, db, cursor, batchSize)
		if err != nil {
			return result, err
		}
		if len(filings) == 0 {
			break
		}
		resolved, err := ResolveFilingObservations(ctx, db, filings)
		if err != nil {
			return result, err
		}
		if _, err := UpsertFilings(ctx, db, ingestRunID, resolved); err != nil {
			return result, err
		}
		result.Attempted += len(resolved)
		for _, filing := range resolved {
			if filing.ResolutionStatus == domain.FilingResolutionResolved {
				result.Resolved++
			} else {
				result.StillPending++
			}
		}
		cursor = nextCursor
	}
	return result, nil
}

func listPendingFilingsAfter(ctx context.Context, db *sql.DB, afterID int64, limit int) ([]domain.FilingObservation, int64, error) {
	rows, err := db.QueryContext(ctx, `
		SELECT
			filing_id, instrument_id, source, source_filing_id, provider_code,
			COALESCE(exchange_mic,''), COALESCE(security_name,''), COALESCE(title,''),
			COALESCE(filing_type,'unknown'), filing_variant, report_period,
			announcement_time, announcement_date, announcement_time_precision, COALESCE(source_url,''), COALESCE(raw_category,''),
			classifier_version, is_correction,
			COALESCE(provider_org_id,''), COALESCE(provider_column_id,''),
			COALESCE(provider_page_column,''), raw_announcement_time_ms,
			catalogue_artifact_id, artifact_id, COALESCE(sha256,''),
			COALESCE(resolution_reason,'')
		FROM fundamental.filing
		WHERE resolution_status='pending' AND filing_id>?
		ORDER BY filing_id
		LIMIT ?
	`, afterID, limit)
	if err != nil {
		return nil, afterID, fmt.Errorf("query pending filings: %w", err)
	}
	defer rows.Close()
	var out []domain.FilingObservation
	next := afterID
	for rows.Next() {
		var filing domain.FilingObservation
		var instrument sql.NullInt64
		var reportPeriod sql.NullTime
		var announcement, announcementDate sql.NullTime
		var rawMillis sql.NullInt64
		var catalogueArtifact sql.NullInt64
		var documentArtifact sql.NullInt64
		var filingType string
		var filingVariant string
		if err := rows.Scan(
			&filing.FilingID, &instrument, &filing.Source, &filing.SourceFilingID, &filing.ProviderCode,
			&filing.ExchangeMIC, &filing.SecurityName, &filing.Title,
			&filingType, &filingVariant, &reportPeriod,
			&announcement, &announcementDate, &filing.AnnouncementTimePrecision, &filing.SourceURL, &filing.RawCategory,
			&filing.ClassifierVersion, &filing.IsCorrection,
			&filing.ProviderOrgID, &filing.ProviderColumnID,
			&filing.ProviderPageColumn, &rawMillis,
			&catalogueArtifact, &documentArtifact, &filing.DocumentSHA256,
			&filing.ResolutionReason,
		); err != nil {
			return nil, afterID, fmt.Errorf("scan pending filing: %w", err)
		}
		filing.FilingType = domain.FilingType(filingType)
		filing.FilingVariant = domain.FilingVariant(filingVariant)
		filing.ResolutionStatus = domain.FilingResolutionPending
		if instrument.Valid {
			filing.InstrumentID = instrument.Int64
		}
		if reportPeriod.Valid {
			v := reportPeriod.Time
			filing.ReportPeriod = &v
		}
		if !announcement.Valid {
			return nil, afterID, fmt.Errorf("pending filing %d has no announcement time", filing.FilingID)
		}
		filing.AnnouncementTime = announcement.Time
		if announcementDate.Valid {
			filing.AnnouncementDate = announcementDate.Time
		}
		if rawMillis.Valid {
			filing.RawAnnouncementTimeMillis = rawMillis.Int64
		}
		if catalogueArtifact.Valid {
			filing.CatalogueArtifactID = catalogueArtifact.Int64
		}
		if documentArtifact.Valid {
			filing.DocumentArtifactID = documentArtifact.Int64
		}
		out = append(out, filing)
		next = filing.FilingID
	}
	if err := rows.Err(); err != nil {
		return nil, afterID, fmt.Errorf("iterate pending filings: %w", err)
	}
	return out, next, nil
}
