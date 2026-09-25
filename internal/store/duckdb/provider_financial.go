package duckdb

import (
	"context"
	"database/sql"
	"database/sql/driver"
	"fmt"
	"math"

	duckdbgo "github.com/duckdb/duckdb-go/v2"
	"github.com/yinhm/alphalake/internal/domain"
)

type FinancialSourceWriteResult struct{ Attempted, Inserted, Reassigned, Removed int }

func nullableInstrument(id int64) any {
	if id == 0 {
		return nil
	}
	return id
}

// ReconcileFinancialSourceRecords stores locators and resolved
// identities only. Immutable numeric evidence lives exclusively in the ZIP.
func ReconcileFinancialSourceRecords(ctx context.Context, db *sql.DB, runID int64, source, sha string, records []domain.ProviderFinancialRecord) (out FinancialSourceWriteResult, err error) {

	conn, err := db.Conn(ctx)
	if err != nil {
		return out, err
	}
	defer conn.Close()
	if _, err = conn.ExecContext(ctx, "BEGIN"); err != nil {
		return out, err
	}
	defer conn.ExecContext(context.WithoutCancel(ctx), "ROLLBACK")
	out, err = reconcileFinancialRecords(ctx, conn, runID, source, sha, records)
	if err != nil {
		return out, err
	}
	_, err = conn.ExecContext(ctx, "COMMIT")
	return out, err
}

func reconcileFinancialRecords(ctx context.Context, conn *sql.Conn, runID int64, source, sha string, records []domain.ProviderFinancialRecord) (out FinancialSourceWriteResult, err error) {
	if source != "tdx" || sha == "" || runID <= 0 {
		return out, fmt.Errorf("TDX revision and run required")
	}
	var artifactID int64
	if err = conn.QueryRowContext(ctx, `SELECT artifact_id FROM meta.artifact WHERE source=? AND sha256=?`, source, sha).Scan(&artifactID); err != nil {
		return out, err
	}
	if artifactID <= 0 || artifactID > (math.MaxInt64-8191)/8192/65536-1 {
		return out, fmt.Errorf("invalid source artifact ID")
	}
	if _, err = conn.ExecContext(ctx, `CREATE TEMP TABLE _source_headers AS SELECT * FROM fundamental.source_record WHERE false`); err != nil {
		return out, err
	}
	defer conn.ExecContext(context.WithoutCancel(ctx), `DROP TABLE IF EXISTS temp.main._source_headers`)
	err = conn.Raw(func(raw any) error {
		app, e := duckdbgo.NewAppender(raw.(driver.Conn), "temp", "main", "_source_headers")
		if e != nil {
			return e
		}
		defer app.Close()
		seen := map[uint32]bool{}
		for _, r := range records {
			if r.Provider != source || r.ArtifactID != artifactID || r.SourceRow == 0 || r.SourceRow > 65535 || r.InstrumentID <= 0 || r.ReportPeriod.IsZero() || !sixDigitCode.MatchString(r.ProviderCode) || len(r.ProviderFields) == 0 || len(r.ProviderFields) > 4096 || seen[r.SourceRow] {
				_ = app.Clear()
				return fmt.Errorf("invalid or duplicate source row locator")
			}
			seen[r.SourceRow] = true
			if e = app.AppendRow(artifactID*65536+int64(r.SourceRow), artifactID, r.SourceRow, r.ProviderCode, r.MarketMarker, uint16(len(r.ProviderFields)), r.ReportPeriod, r.InstrumentID); e != nil {
				_ = app.Clear()
				return e
			}
		}
		return app.Flush()
	})
	if err != nil {
		return out, err
	}
	if !domain.IncludesBSE(ctx) {
		if _, err = conn.ExecContext(ctx, `DELETE FROM _source_headers WHERE instrument_id IN (SELECT instrument_id FROM core.instrument WHERE exchange_mic='XBSE')`); err != nil {
			return out, err
		}
	}

	var changedEvidence bool
	if err = conn.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM _source_headers s JOIN fundamental.source_record old USING(source_record_id) WHERE s.artifact_id IS DISTINCT FROM old.artifact_id OR s.source_row IS DISTINCT FROM old.source_row OR s.provider_code IS DISTINCT FROM old.provider_code OR s.market_marker IS DISTINCT FROM old.market_marker OR s.field_count IS DISTINCT FROM old.field_count OR s.report_period IS DISTINCT FROM old.report_period)`).Scan(&changedEvidence); err != nil {
		return out, err
	}
	if changedEvidence {
		return out, fmt.Errorf("immutable financial locator changed")
	}
	if err = conn.QueryRowContext(ctx, `SELECT count(*),count(*) FILTER(WHERE old.source_record_id IS NULL),count(*) FILTER(WHERE old.source_record_id IS NOT NULL AND old.instrument_id IS DISTINCT FROM s.instrument_id) FROM _source_headers s LEFT JOIN fundamental.source_record old USING(source_record_id)`).Scan(&out.Attempted, &out.Inserted, &out.Reassigned); err != nil {
		return out, err
	}
	stale := `artifact_id=? AND instrument_id IS NOT NULL AND source_record_id NOT IN(SELECT source_record_id FROM _source_headers)`
	if !domain.IncludesBSE(ctx) {
		stale += ` AND instrument_id NOT IN(SELECT instrument_id FROM core.instrument WHERE exchange_mic='XBSE')`
	}
	if err = conn.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.source_record WHERE `+stale, artifactID).Scan(&out.Removed); err != nil {
		return out, err
	}
	if _, err = conn.ExecContext(ctx, `UPDATE fundamental.source_record SET instrument_id=NULL WHERE `+stale, artifactID); err != nil {
		return out, err
	}
	if _, err = conn.ExecContext(ctx, `INSERT INTO fundamental.source_record SELECT * FROM _source_headers ON CONFLICT(source_record_id) DO UPDATE SET instrument_id=excluded.instrument_id WHERE source_record.instrument_id IS DISTINCT FROM excluded.instrument_id`); err != nil {
		return out, err
	}
	// Withdraw unsupported standard rows in the same transaction as identity repair.
	if _, err = conn.ExecContext(ctx, `DELETE FROM fundamental.statement_snapshot s USING fundamental.source_record r WHERE s.source_record_id=r.source_record_id AND r.artifact_id=? AND s.instrument_id IS DISTINCT FROM r.instrument_id`, artifactID); err != nil {
		return out, err
	}
	_, err = conn.ExecContext(ctx, `DROP TABLE _source_headers`)
	return out, err
}

// PublishFinancialPackage commits identity governance, source locators and the
// matching completeness checkpoint atomically. Failed packages remain retryable.
func PublishFinancialPackage(ctx context.Context, db *sql.DB, runID int64, sha string, records []domain.ProviderFinancialRecord, inputs []ProviderFinancialResolutionInput, checkpointKey, checkpointValue string) (state ProviderFinancialResolutionApplyResult, written FinancialSourceWriteResult, err error) {
	conn, err := db.Conn(ctx)
	if err != nil {
		return state, written, err
	}
	defer conn.Close()
	if _, err = conn.ExecContext(ctx, "BEGIN"); err != nil {
		return state, written, err
	}
	defer conn.ExecContext(context.WithoutCancel(ctx), "ROLLBACK")
	state, err = applyProviderFinancialResolutions(ctx, conn, runID, inputs)
	if err != nil {
		return state, written, err
	}
	written, err = reconcileFinancialRecords(ctx, conn, runID, "tdx", sha, records)
	if err != nil {
		return state, written, err
	}
	if state.Pending == 0 {
		if _, err = conn.ExecContext(ctx, `INSERT INTO meta.checkpoint(source,dataset,checkpoint_key,checkpoint_value) VALUES('tdx','professional_financial',?,?) ON CONFLICT(source,dataset,checkpoint_key) DO UPDATE SET checkpoint_value=excluded.checkpoint_value,updated_at=now()`, checkpointKey, checkpointValue); err != nil {
			return state, written, err
		}
	}
	_, err = conn.ExecContext(ctx, "COMMIT")
	return state, written, err
}
