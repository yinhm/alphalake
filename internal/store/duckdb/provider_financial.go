package duckdb

import (
	"context"
	"database/sql"
	"database/sql/driver"
	"errors"
	"fmt"
	"strings"

	duckdbgo "github.com/duckdb/duckdb-go/v2"
	"github.com/yinhm/alphalake/internal/domain"
)

const providerFactStageTable = "_alphalake_provider_fact_stage"

type ProviderFactWriteResult struct {
	Attempted  int
	Inserted   int
	Reassigned int
	Removed    int
}

// ReconcileProviderFinancialRecordsForArtifact publishes the currently-resolved
// provider facts for one immutable artifact revision. Raw provider identity is
// (source, revision_key, provider_code, provider_field); canonical instrument_id
// is an enrichable link and may change when historical lifecycle evidence is
// corrected. The transaction therefore:
//   - backfills raw identity onto legacy rows when safely inferable from staging;
//   - removes facts that are no longer resolved for this artifact revision;
//   - updates an existing raw fact in place when its canonical instrument changes;
//   - inserts only genuinely new raw facts.
//
// This prevents one immutable provider revision from accumulating duplicate facts
// under old and corrected canonical instruments.
func ReconcileProviderFinancialRecordsForArtifact(
	ctx context.Context,
	db *sql.DB,
	ingestRunID int64,
	source string,
	artifactSHA string,
	records []domain.ProviderFinancialRecord,
) (ProviderFactWriteResult, error) {
	var result ProviderFactWriteResult
	if db == nil {
		return result, errors.New("duckdb is nil")
	}
	if ingestRunID <= 0 {
		return result, errors.New("ingest run ID must be positive")
	}
	source = strings.TrimSpace(source)
	artifactSHA = strings.TrimSpace(artifactSHA)
	if source == "" || artifactSHA == "" {
		return result, errors.New("source and artifact sha256 are required")
	}
	for i, record := range records {
		if record.InstrumentID <= 0 || record.ReportPeriod.IsZero() || record.ArtifactID <= 0 {
			return result, fmt.Errorf("provider financial record %d has incomplete canonical identity/lineage", i)
		}
		if strings.TrimSpace(record.Provider) != source || strings.TrimSpace(record.ProviderCode) == "" || strings.TrimSpace(record.SourceFile) == "" {
			return result, fmt.Errorf("provider financial record %d has incomplete/mismatched source identity", i)
		}
		result.Attempted += len(record.ProviderFields)
	}

	conn, err := db.Conn(ctx)
	if err != nil {
		return result, fmt.Errorf("acquire provider-fact connection: %w", err)
	}
	defer conn.Close()
	if _, err := conn.ExecContext(ctx, `DROP TABLE IF EXISTS temp.main.`+providerFactStageTable); err != nil {
		return result, fmt.Errorf("cleanup provider-fact staging table: %w", err)
	}
	if _, err := conn.ExecContext(ctx, `BEGIN TRANSACTION`); err != nil {
		return result, fmt.Errorf("begin provider-fact reconcile: %w", err)
	}
	committed := false
	defer func() {
		if !committed {
			_, _ = conn.ExecContext(context.Background(), `ROLLBACK`)
		}
		_, _ = conn.ExecContext(context.Background(), `DROP TABLE IF EXISTS temp.main.`+providerFactStageTable)
	}()

	if _, err := conn.ExecContext(ctx, `
		CREATE TEMP TABLE `+providerFactStageTable+` (
			instrument_id BIGINT NOT NULL,
			source VARCHAR NOT NULL,
			report_period DATE NOT NULL,
			announcement_time TIMESTAMPTZ,
			provider_code VARCHAR NOT NULL,
			market_marker USMALLINT NOT NULL,
			provider_field VARCHAR NOT NULL,
			value DOUBLE,
			value_float32_bits UBIGINT,
			source_file VARCHAR,
			source_file_hash VARCHAR,
			artifact_id BIGINT,
			ingest_run_id BIGINT,
			revision_key VARCHAR NOT NULL
		)
	`); err != nil {
		return result, fmt.Errorf("create provider-fact staging table: %w", err)
	}
	if len(records) > 0 {
		if err := appendProviderFacts(ctx, conn, ingestRunID, artifactSHA, records); err != nil {
			return result, err
		}
	}

	// Migration 012 deliberately leaves pre-existing rows with provider_code=NULL.
	// On first replay, attach a raw code only where one canonical instrument maps
	// to exactly one provider code in this artifact revision.
	if _, err := conn.ExecContext(ctx, `
		WITH mapping AS (
			SELECT instrument_id, source, report_period, revision_key,
			       min(provider_code) AS provider_code,
			       min(market_marker) AS market_marker
			FROM temp.main.`+providerFactStageTable+`
			GROUP BY instrument_id, source, report_period, revision_key
			HAVING count(DISTINCT provider_code)=1
		)
		UPDATE fundamental.provider_fact AS p
		SET provider_code=m.provider_code, market_marker=m.market_marker
		FROM mapping m
		WHERE p.provider_code IS NULL
		  AND p.instrument_id=m.instrument_id
		  AND p.source=m.source
		  AND p.report_period=m.report_period
		  AND p.revision_key=m.revision_key
	`); err != nil {
		return result, fmt.Errorf("backfill legacy provider-fact raw identity: %w", err)
	}

	// Facts absent from the currently-resolved stage are stale. This includes a
	// record that became unresolved and legacy duplicate facts left under an old
	// canonical instrument before raw identity was persisted.
	stalePredicate := `
		p.source=? AND p.revision_key=? AND (
			p.provider_code IS NULL OR NOT EXISTS (
				SELECT 1 FROM temp.main.`+providerFactStageTable+` s
				WHERE s.source=p.source
				  AND s.revision_key=p.revision_key
				  AND s.provider_code=p.provider_code
				  AND s.provider_field=p.provider_field
			)
		)`
	if err := conn.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.provider_fact p WHERE `+stalePredicate, source, artifactSHA).Scan(&result.Removed); err != nil {
		return result, fmt.Errorf("count stale provider financial facts: %w", err)
	}
	if _, err := conn.ExecContext(ctx, `DELETE FROM fundamental.provider_fact p WHERE `+stalePredicate, source, artifactSHA); err != nil {
		return result, fmt.Errorf("remove stale provider financial facts: %w", err)
	}

	if err := conn.QueryRowContext(ctx, `
		SELECT count(*)
		FROM (
			SELECT DISTINCT source, revision_key, provider_code, provider_field, instrument_id
			FROM temp.main.`+providerFactStageTable+`
		) s
		JOIN fundamental.provider_fact p
		  ON p.source=s.source
		 AND p.revision_key=s.revision_key
		 AND p.provider_code=s.provider_code
		 AND p.provider_field=s.provider_field
		WHERE p.instrument_id<>s.instrument_id
	`).Scan(&result.Reassigned); err != nil {
		return result, fmt.Errorf("count reassigned provider financial facts: %w", err)
	}

	if err := conn.QueryRowContext(ctx, `
		SELECT count(*)
		FROM (
			SELECT DISTINCT source, revision_key, provider_code, provider_field
			FROM temp.main.`+providerFactStageTable+`
		) s
		WHERE NOT EXISTS (
			SELECT 1 FROM fundamental.provider_fact p
			WHERE p.source=s.source
			  AND p.revision_key=s.revision_key
			  AND p.provider_code=s.provider_code
			  AND p.provider_field=s.provider_field
		)
	`).Scan(&result.Inserted); err != nil {
		return result, fmt.Errorf("count new provider financial facts: %w", err)
	}

	if _, err := conn.ExecContext(ctx, `
		INSERT INTO fundamental.provider_fact (
			instrument_id, source, report_period, announcement_time,
			provider_code, market_marker, provider_field, value,
			source_file, source_file_hash, artifact_id, ingest_run_id,
			revision_key, value_float32_bits
		)
		SELECT
			instrument_id, source, report_period, announcement_time,
			provider_code, market_marker, provider_field, value,
			source_file, source_file_hash, artifact_id, ingest_run_id,
			revision_key, value_float32_bits
		FROM temp.main.`+providerFactStageTable+`
		ON CONFLICT (source, revision_key, provider_code, provider_field) DO UPDATE SET
			instrument_id=excluded.instrument_id,
			report_period=excluded.report_period,
			announcement_time=excluded.announcement_time,
			market_marker=excluded.market_marker,
			value=excluded.value,
			value_float32_bits=excluded.value_float32_bits,
			source_file=excluded.source_file,
			source_file_hash=excluded.source_file_hash,
			artifact_id=excluded.artifact_id,
			ingest_run_id=excluded.ingest_run_id,
			ingested_at=now()
	`); err != nil {
		return result, fmt.Errorf("merge provider financial facts: %w", err)
	}
	if _, err := conn.ExecContext(ctx, `DROP TABLE temp.main.`+providerFactStageTable); err != nil {
		return result, fmt.Errorf("drop provider-fact staging table: %w", err)
	}
	if _, err := conn.ExecContext(ctx, `COMMIT`); err != nil {
		return result, fmt.Errorf("commit provider-fact reconcile: %w", err)
	}
	committed = true
	return result, nil
}

// InsertProviderFinancialRecordsForArtifact is retained as a compatibility
// wrapper for callers that only append resolved rows. New ingestion code should
// use ReconcileProviderFinancialRecordsForArtifact so a revision can also remove
// facts that became unresolved after an identity correction.
func InsertProviderFinancialRecordsForArtifact(
	ctx context.Context,
	db *sql.DB,
	ingestRunID int64,
	artifactSHA string,
	records []domain.ProviderFinancialRecord,
) (ProviderFactWriteResult, error) {
	if len(records) == 0 {
		return ProviderFactWriteResult{}, nil
	}
	return ReconcileProviderFinancialRecordsForArtifact(ctx, db, ingestRunID, records[0].Provider, artifactSHA, records)
}

func appendProviderFacts(ctx context.Context, conn *sql.Conn, ingestRunID int64, artifactSHA string, records []domain.ProviderFinancialRecord) error {
	return conn.Raw(func(raw any) error {
		driverConn, ok := raw.(driver.Conn)
		if !ok {
			return errors.New("duckdb raw connection does not implement driver.Conn")
		}
		appender, err := duckdbgo.NewAppender(driverConn, "temp", "main", providerFactStageTable)
		if err != nil {
			return fmt.Errorf("create provider-fact appender: %w", err)
		}
		for _, record := range records {
			var announcement driver.Value
			if record.AnnouncementTime != nil {
				announcement = *record.AnnouncementTime
			}
			for i, field := range record.ProviderFields {
				if err := appender.AppendRow(
					record.InstrumentID,
					record.Provider,
					record.ReportPeriod,
					announcement,
					record.ProviderCode,
					uint16(record.MarketMarker),
					fmt.Sprintf("FN%d", i+1),
					field.Value,
					int64(field.Bits),
					record.SourceFile,
					artifactSHA,
					record.ArtifactID,
					ingestRunID,
					artifactSHA,
				); err != nil {
					_ = appender.Clear()
					_ = appender.Close()
					return fmt.Errorf("append provider fact instrument=%d code=%s field=FN%d: %w", record.InstrumentID, record.ProviderCode, i+1, err)
				}
			}
		}
		if err := appender.CloseWithCancel(ctx); err != nil {
			return fmt.Errorf("flush provider-fact appender: %w", err)
		}
		return nil
	})
}
