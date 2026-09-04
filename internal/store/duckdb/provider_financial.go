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
	Attempted int
	Inserted  int
}

// InsertProviderFinancialRecordsForArtifact appends one immutable artifact
// revision of provider facts. The revision key is the artifact SHA-256, so a
// corrected package with the same report period remains queryable alongside the
// prior revision instead of destructively overwriting history. Attempted counts
// staged facts; Inserted counts only rows absent from the canonical target before
// this merge, making idempotent replay observable as Inserted=0.
func InsertProviderFinancialRecordsForArtifact(
	ctx context.Context,
	db *sql.DB,
	ingestRunID int64,
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
	artifactSHA = strings.TrimSpace(artifactSHA)
	if artifactSHA == "" {
		return result, errors.New("artifact sha256 is required")
	}
	if len(records) == 0 {
		return result, nil
	}
	for i, record := range records {
		if record.InstrumentID <= 0 || record.ReportPeriod.IsZero() || record.ArtifactID <= 0 {
			return result, fmt.Errorf("provider financial record %d has incomplete canonical identity/lineage", i)
		}
		if strings.TrimSpace(record.Provider) == "" || strings.TrimSpace(record.SourceFile) == "" {
			return result, fmt.Errorf("provider financial record %d has incomplete source identity", i)
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
		return result, fmt.Errorf("begin provider-fact bulk insert: %w", err)
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
	if err := appendProviderFacts(ctx, conn, ingestRunID, artifactSHA, records); err != nil {
		return result, err
	}
	if err := conn.QueryRowContext(ctx, `
		SELECT count(*)
		FROM (
			SELECT DISTINCT instrument_id, source, report_period, provider_field, revision_key
			FROM temp.main.`+providerFactStageTable+`
		) s
		WHERE NOT EXISTS (
			SELECT 1
			FROM fundamental.provider_fact p
			WHERE p.instrument_id=s.instrument_id
			  AND p.source=s.source
			  AND p.report_period=s.report_period
			  AND p.provider_field=s.provider_field
			  AND p.revision_key=s.revision_key
		)
	`).Scan(&result.Inserted); err != nil {
		return result, fmt.Errorf("count new provider financial facts: %w", err)
	}
	if _, err := conn.ExecContext(ctx, `
		INSERT INTO fundamental.provider_fact (
			instrument_id, source, report_period, announcement_time,
			provider_field, value, source_file, source_file_hash,
			artifact_id, ingest_run_id, revision_key, value_float32_bits
		)
		SELECT
			instrument_id, source, report_period, announcement_time,
			provider_field, value, source_file, source_file_hash,
			artifact_id, ingest_run_id, revision_key, value_float32_bits
		FROM temp.main.`+providerFactStageTable+`
		ON CONFLICT (instrument_id, source, report_period, provider_field, revision_key) DO NOTHING
	`); err != nil {
		return result, fmt.Errorf("merge provider financial facts: %w", err)
	}
	if _, err := conn.ExecContext(ctx, `DROP TABLE temp.main.`+providerFactStageTable); err != nil {
		return result, fmt.Errorf("drop provider-fact staging table: %w", err)
	}
	if _, err := conn.ExecContext(ctx, `COMMIT`); err != nil {
		return result, fmt.Errorf("commit provider-fact bulk insert: %w", err)
	}
	committed = true
	return result, nil
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
					return fmt.Errorf("append provider fact instrument=%d field=FN%d: %w", record.InstrumentID, i+1, err)
				}
			}
		}
		if err := appender.CloseWithCancel(ctx); err != nil {
			return fmt.Errorf("flush provider-fact appender: %w", err)
		}
		return nil
	})
}
