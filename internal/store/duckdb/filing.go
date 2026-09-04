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

const filingResolverVersion = "filing-identity-v1"

type FilingWriteResult struct {
	Attempted int
	Inserted  int
	Updated   int
	Resolved  int
	Pending   int
	Documents int
}

// ResolveFilingObservations resolves authoritative filing observations through
// temporal TDX identifiers. Explicit exchange evidence is authoritative for the
// candidate prefix; absent exchange evidence falls back to a strict equity-only
// raw-code search. Unknown non-empty exchange evidence remains pending rather
// than being silently discarded. No current code-range classifier is consulted.
func ResolveFilingObservations(ctx context.Context, db *sql.DB, filings []domain.FilingObservation) ([]domain.FilingObservation, error) {
	if db == nil {
		return nil, errors.New("duckdb is nil")
	}
	out := make([]domain.FilingObservation, len(filings))
	copy(out, filings)
	for i := range out {
		filing := &out[i]
		filing.ProviderCode = strings.TrimSpace(filing.ProviderCode)
		filing.ExchangeMIC = strings.TrimSpace(filing.ExchangeMIC)
		if filing.ProviderCode == "" || filing.AnnouncementTime.IsZero() {
			return nil, fmt.Errorf("filing %d requires provider code and announcement time", i)
		}
		if filing.ExchangeMIC != "" {
			if _, ok := filingExchangePrefix(filing.ExchangeMIC); !ok {
				filing.InstrumentID = 0
				filing.ResolutionStatus = domain.FilingResolutionPending
				filing.ResolutionReason = fmt.Sprintf("unsupported filing exchange evidence %q for provider code %s", filing.ExchangeMIC, filing.ProviderCode)
				continue
			}
		}
		instrumentID, candidates, err := resolveFilingInstrument(ctx, db, filing.ProviderCode, filing.ExchangeMIC, filing.AnnouncementTime)
		if err != nil {
			return nil, fmt.Errorf("resolve filing %s: %w", filing.SourceFilingID, err)
		}
		if instrumentID > 0 {
			filing.InstrumentID = instrumentID
			filing.ResolutionStatus = domain.FilingResolutionResolved
			filing.ResolutionReason = ""
			continue
		}
		filing.InstrumentID = 0
		filing.ResolutionStatus = domain.FilingResolutionPending
		if len(candidates) == 0 {
			if filing.ExchangeMIC == "" {
				filing.ResolutionReason = fmt.Sprintf("no temporal TDX equity identifier for raw code %s at %s", filing.ProviderCode, dateUTC(filing.AnnouncementTime).Format("2006-01-02"))
			} else {
				filing.ResolutionReason = fmt.Sprintf("no temporal TDX equity identifier for %s/%s at %s", filing.ExchangeMIC, filing.ProviderCode, dateUTC(filing.AnnouncementTime).Format("2006-01-02"))
			}
		} else {
			filing.ResolutionReason = fmt.Sprintf("ambiguous temporal TDX equity identifiers for raw code %s: %s", filing.ProviderCode, strings.Join(candidates, ","))
		}
	}
	return out, nil
}

func resolveFilingInstrument(ctx context.Context, db *sql.DB, code, exchangeMIC string, asOf time.Time) (int64, []string, error) {
	if !sixDigitProviderCode(code) {
		return 0, nil, fmt.Errorf("provider code %q is not six digits", code)
	}
	asOf = dateUTC(asOf)
	if prefix, ok := filingExchangePrefix(exchangeMIC); ok {
		identifier := prefix + code
		rows, err := db.QueryContext(ctx, `
			SELECT x.instrument_id
			FROM ref.instrument_identifier x
			JOIN ref.instrument i ON i.instrument_id=x.instrument_id
			WHERE x.provider='tdx'
			  AND x.identifier_type='symbol'
			  AND x.identifier_value=?
			  AND i.instrument_type='equity'
			  AND (x.valid_from IS NULL OR x.valid_from <= ?)
			  AND (x.valid_to IS NULL OR x.valid_to > ?)
		`, identifier, asOf, asOf)
		if err != nil {
			return 0, nil, fmt.Errorf("query exchange-qualified filing identifier: %w", err)
		}
		defer rows.Close()
		var ids []int64
		for rows.Next() {
			var id int64
			if err := rows.Scan(&id); err != nil {
				return 0, nil, fmt.Errorf("scan exchange-qualified filing identifier: %w", err)
			}
			ids = append(ids, id)
		}
		if err := rows.Err(); err != nil {
			return 0, nil, fmt.Errorf("iterate exchange-qualified filing identifier: %w", err)
		}
		switch len(ids) {
		case 0:
			return 0, nil, nil
		case 1:
			return ids[0], []string{identifier}, nil
		default:
			return 0, nil, fmt.Errorf("overlapping temporal TDX identifier %s at %s", identifier, asOf.Format("2006-01-02"))
		}
	}

	rows, err := db.QueryContext(ctx, `
		SELECT x.instrument_id, x.identifier_value
		FROM ref.instrument_identifier x
		JOIN ref.instrument i ON i.instrument_id=x.instrument_id
		WHERE x.provider='tdx'
		  AND x.identifier_type='symbol'
		  AND i.instrument_type='equity'
		  AND right(x.identifier_value, 6)=?
		  AND (x.valid_from IS NULL OR x.valid_from <= ?)
		  AND (x.valid_to IS NULL OR x.valid_to > ?)
		ORDER BY x.identifier_value
	`, code, asOf, asOf)
	if err != nil {
		return 0, nil, fmt.Errorf("query raw-code filing identifiers: %w", err)
	}
	defer rows.Close()
	byIdentifier := map[string]int64{}
	for rows.Next() {
		var id int64
		var identifier string
		if err := rows.Scan(&id, &identifier); err != nil {
			return 0, nil, fmt.Errorf("scan raw-code filing identifier: %w", err)
		}
		identifier = strings.TrimSpace(identifier)
		if prior, exists := byIdentifier[identifier]; exists && prior != id {
			return 0, nil, fmt.Errorf("overlapping temporal TDX identifier %s at %s", identifier, asOf.Format("2006-01-02"))
		}
		byIdentifier[identifier] = id
	}
	if err := rows.Err(); err != nil {
		return 0, nil, fmt.Errorf("iterate raw-code filing identifiers: %w", err)
	}
	candidates := make([]string, 0, len(byIdentifier))
	var instrumentID int64
	for identifier, id := range byIdentifier {
		candidates = append(candidates, identifier)
		instrumentID = id
	}
	sortStrings(candidates)
	if len(candidates) != 1 {
		return 0, candidates, nil
	}
	return instrumentID, candidates, nil
}

func filingExchangePrefix(exchangeMIC string) (string, bool) {
	switch strings.ToUpper(strings.TrimSpace(exchangeMIC)) {
	case "XSHG":
		return "sh", true
	case "XSHE":
		return "sz", true
	case "XBSE":
		return "bj", true
	case "":
		return "", false
	default:
		return "", false
	}
}

// UpsertFilings records one catalogue page's normalized filing observations.
// Source filing IDs are stable provider identity; re-observation updates metadata
// and resolution while retaining first_seen_at. Document revisions are immutable
// artifact links and are never overwritten.
func UpsertFilings(ctx context.Context, db *sql.DB, ingestRunID int64, filings []domain.FilingObservation) (FilingWriteResult, error) {
	var result FilingWriteResult
	if db == nil {
		return result, errors.New("duckdb is nil")
	}
	if ingestRunID <= 0 {
		return result, errors.New("ingest run ID must be positive")
	}
	if len(filings) == 0 {
		return result, nil
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return result, fmt.Errorf("begin filing upsert: %w", err)
	}
	defer tx.Rollback()

	for i := range filings {
		filing := filings[i]
		result.Attempted++
		if err := validateFilingObservation(filing); err != nil {
			return result, fmt.Errorf("filing %d: %w", i, err)
		}
		filing.Source = strings.TrimSpace(filing.Source)
		filing.SourceFilingID = strings.TrimSpace(filing.SourceFilingID)
		filing.ProviderCode = strings.TrimSpace(filing.ProviderCode)
		status := strings.TrimSpace(filing.ResolutionStatus)
		if status == "" {
			if filing.InstrumentID > 0 {
				status = domain.FilingResolutionResolved
			} else {
				status = domain.FilingResolutionPending
			}
		}

		var existingID int64
		err := tx.QueryRowContext(ctx, `
			SELECT filing_id FROM fundamental.filing
			WHERE source=? AND source_filing_id=?
		`, filing.Source, filing.SourceFilingID).Scan(&existingID)
		exists := err == nil
		if err != nil && !errors.Is(err, sql.ErrNoRows) {
			return result, fmt.Errorf("query filing identity: %w", err)
		}
		var instrument any
		if filing.InstrumentID > 0 {
			instrument = filing.InstrumentID
		}
		var reportPeriod any
		if filing.ReportPeriod != nil {
			reportPeriod = dateUTC(*filing.ReportPeriod)
		}
		var catalogueArtifact any
		if filing.CatalogueArtifactID > 0 {
			catalogueArtifact = filing.CatalogueArtifactID
		}
		var documentArtifact any
		if filing.DocumentArtifactID > 0 {
			documentArtifact = filing.DocumentArtifactID
		}
		if !exists {
			if err := tx.QueryRowContext(ctx, `
				INSERT INTO fundamental.filing (
					instrument_id, source, source_filing_id, provider_code,
					exchange_mic, security_name, filing_type, filing_variant,
					report_period, announcement_time, title, source_url,
					raw_category, classifier_version, is_correction,
					resolution_status, resolution_reason, catalogue_artifact_id,
					artifact_id, sha256, provider_org_id, provider_column_id,
					provider_page_column, raw_announcement_time_ms, ingest_run_id,
					first_seen_at, last_seen_at
				) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now(), now())
				RETURNING filing_id
			`, instrument, filing.Source, filing.SourceFilingID, filing.ProviderCode,
				nullableString(filing.ExchangeMIC), nullableString(filing.SecurityName), string(filing.FilingType), string(filing.FilingVariant),
				reportPeriod, filing.AnnouncementTime.UTC(), nullableString(filing.Title), nullableString(filing.SourceURL),
				nullableString(filing.RawCategory), filing.ClassifierVersion, filing.IsCorrection,
				status, nullableString(filing.ResolutionReason), catalogueArtifact,
				documentArtifact, nullableString(filing.DocumentSHA256), nullableString(filing.ProviderOrgID), nullableString(filing.ProviderColumnID),
				nullableString(filing.ProviderPageColumn), nullableInt64(filing.RawAnnouncementTimeMillis), ingestRunID,
			).Scan(&existingID); err != nil {
				return result, fmt.Errorf("insert filing %s: %w", filing.SourceFilingID, err)
			}
			result.Inserted++
		} else {
			if _, err := tx.ExecContext(ctx, `
				UPDATE fundamental.filing SET
					instrument_id=?, provider_code=?, exchange_mic=?, security_name=?,
					filing_type=?, filing_variant=?, report_period=?, announcement_time=?,
					title=?, source_url=?, raw_category=?, classifier_version=?,
					is_correction=?, resolution_status=?, resolution_reason=?,
					catalogue_artifact_id=COALESCE(?, catalogue_artifact_id),
					artifact_id=COALESCE(?, artifact_id),
					sha256=COALESCE(?, sha256),
					provider_org_id=?, provider_column_id=?, provider_page_column=?,
					raw_announcement_time_ms=?, ingest_run_id=?, last_seen_at=now()
				WHERE filing_id=?
			`, instrument, filing.ProviderCode, nullableString(filing.ExchangeMIC), nullableString(filing.SecurityName),
				string(filing.FilingType), string(filing.FilingVariant), reportPeriod, filing.AnnouncementTime.UTC(),
				nullableString(filing.Title), nullableString(filing.SourceURL), nullableString(filing.RawCategory), filing.ClassifierVersion,
				filing.IsCorrection, status, nullableString(filing.ResolutionReason), catalogueArtifact,
				documentArtifact, nullableString(filing.DocumentSHA256), nullableString(filing.ProviderOrgID), nullableString(filing.ProviderColumnID), nullableString(filing.ProviderPageColumn),
				nullableInt64(filing.RawAnnouncementTimeMillis), ingestRunID, existingID,
			); err != nil {
				return result, fmt.Errorf("update filing %s: %w", filing.SourceFilingID, err)
			}
			result.Updated++
		}

		if status == domain.FilingResolutionResolved {
			result.Resolved++
		} else {
			result.Pending++
		}
		if filing.DocumentArtifactID > 0 {
			if filing.SourceURL == "" || filing.DocumentSHA256 == "" {
				return result, fmt.Errorf("filing %s document artifact requires source URL and sha256", filing.SourceFilingID)
			}
			if _, err := tx.ExecContext(ctx, `
				INSERT INTO fundamental.filing_document (
					filing_id, artifact_id, source_url, sha256, fetched_at, ingest_run_id
				) VALUES (?, ?, ?, ?, now(), ?)
				ON CONFLICT(filing_id, artifact_id) DO NOTHING
			`, existingID, filing.DocumentArtifactID, filing.SourceURL, filing.DocumentSHA256, ingestRunID); err != nil {
				return result, fmt.Errorf("link filing document %s: %w", filing.SourceFilingID, err)
			}
			result.Documents++
		}
		if filing.IsCorrection && filing.InstrumentID > 0 && filing.ReportPeriod != nil {
			if err := linkCorrectionTx(ctx, tx, existingID, filing); err != nil {
				return result, err
			}
		}
	}
	if err := tx.Commit(); err != nil {
		return result, fmt.Errorf("commit filing upsert: %w", err)
	}
	return result, nil
}

func validateFilingObservation(filing domain.FilingObservation) error {
	if strings.TrimSpace(filing.Source) == "" || strings.TrimSpace(filing.SourceFilingID) == "" || strings.TrimSpace(filing.ProviderCode) == "" {
		return errors.New("source, source filing ID, and provider code are required")
	}
	if filing.AnnouncementTime.IsZero() {
		return errors.New("announcement time is required")
	}
	if strings.TrimSpace(filing.ClassifierVersion) == "" {
		return errors.New("classifier version is required")
	}
	if filing.InstrumentID > 0 && filing.ResolutionStatus == domain.FilingResolutionPending {
		return errors.New("resolved filing cannot have pending status")
	}
	return nil
}

func linkCorrectionTx(ctx context.Context, tx *sql.Tx, filingID int64, filing domain.FilingObservation) error {
	var prior sql.NullInt64
	err := tx.QueryRowContext(ctx, `
		SELECT filing_id
		FROM fundamental.filing
		WHERE filing_id<>?
		  AND instrument_id=?
		  AND report_period=?
		  AND resolution_status='resolved'
		  AND filing_type=?
		  AND filing_variant IN ('full','corrected_report','revision','correction_notice')
		  AND announcement_time < ?
		ORDER BY announcement_time DESC, filing_id DESC
		LIMIT 1
	`, filingID, filing.InstrumentID, dateUTC(*filing.ReportPeriod), string(filing.FilingType), filing.AnnouncementTime.UTC()).Scan(&prior)
	if errors.Is(err, sql.ErrNoRows) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("resolve correction predecessor for filing %s: %w", filing.SourceFilingID, err)
	}
	if prior.Valid {
		if _, err := tx.ExecContext(ctx, `UPDATE fundamental.filing SET corrects_filing_id=? WHERE filing_id=?`, prior.Int64, filingID); err != nil {
			return fmt.Errorf("link correction predecessor for filing %s: %w", filing.SourceFilingID, err)
		}
	}
	return nil
}

func nullableInt64(value int64) any {
	if value == 0 {
		return nil
	}
	return value
}

func sortStrings(values []string) {
	for i := 1; i < len(values); i++ {
		for j := i; j > 0 && values[j] < values[j-1]; j-- {
			values[j], values[j-1] = values[j-1], values[j]
		}
	}
}
