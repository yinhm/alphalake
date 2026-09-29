package duckdb

import (
	"context"
	"database/sql"
	"fmt"
	"regexp"
	"strings"

	"github.com/yinhm/alphalake/internal/domain"
)

var sourcePositionName = regexp.MustCompile(`^FN[1-9][0-9]*$`)

var standardSnapshotName = regexp.MustCompile(`^[a-z][a-z0-9_]*$`)

// SnapshotField is the standard numeric catalogue, not a source-position schema.
type SnapshotField struct{ Name, Unit, Kind, Basis string }

func LoadSnapshotFields(ctx context.Context, db *sql.DB) ([]SnapshotField, error) {
	return loadSnapshotFields(ctx, db)
}

type snapshotDB interface {
	QueryContext(context.Context, string, ...any) (*sql.Rows, error)
	ExecContext(context.Context, string, ...any) (sql.Result, error)
}

func loadSnapshotFields(ctx context.Context, db snapshotDB) ([]SnapshotField, error) {
	rows, err := db.QueryContext(ctx, `SELECT canonical_field,unit,value_kind,period_basis FROM fundamental.field ORDER BY canonical_field`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []SnapshotField
	for rows.Next() {
		var f SnapshotField
		if err = rows.Scan(&f.Name, &f.Unit, &f.Kind, &f.Basis); err != nil {
			return nil, err
		}
		if !standardSnapshotName.MatchString(f.Name) {
			return nil, fmt.Errorf("invalid standard name %q", f.Name)
		}
		out = append(out, f)
	}
	return out, rows.Err()
}

// CreateFinancialSnapshotTables creates the replacement physical financial
// storage in a rebuild transaction. It does not install a parallel runtime path.
func CreateFinancialSnapshotTables(ctx context.Context, tx *sql.Tx, fields []SnapshotField) error {
	if len(fields) == 0 {
		return fmt.Errorf("empty standard catalogue")
	}
	columns := make([]string, len(fields))
	for i, f := range fields {
		if !standardSnapshotName.MatchString(f.Name) {
			return fmt.Errorf("invalid standard name")
		}
		columns[i] = `"` + f.Name + `" DECIMAL(38,10)`
	}
	_, err := tx.ExecContext(ctx, `
 CREATE TABLE fundamental.materialization_state(artifact_id BIGINT PRIMARY KEY,input_signature VARCHAR NOT NULL,candidates BIGINT NOT NULL,materialized BIGINT NOT NULL,rejected BIGINT NOT NULL);
 CREATE TABLE fundamental.source_record(
  source_record_id BIGINT PRIMARY KEY,artifact_id BIGINT NOT NULL,source_row UINTEGER NOT NULL,
  provider_code VARCHAR NOT NULL,market_marker UTINYINT NOT NULL,field_count USMALLINT NOT NULL,
 report_period DATE NOT NULL,instrument_id BIGINT,
  CHECK(source_row>0),UNIQUE(artifact_id,source_row));
 CREATE TABLE fundamental.statement_snapshot(
  source_record_id BIGINT PRIMARY KEY,instrument_id BIGINT NOT NULL,source_filing_id BIGINT,
  report_period DATE NOT NULL,announcement_time TIMESTAMPTZ,
  ingest_run_id BIGINT NOT NULL,announcement_source VARCHAR NOT NULL DEFAULT 'cninfo',`+strings.Join(columns, ",")+`);
 CREATE TABLE fundamental.statement_field_run(source_record_id BIGINT NOT NULL,canonical_field VARCHAR NOT NULL,ingest_run_id BIGINT NOT NULL,PRIMARY KEY(source_record_id,canonical_field));
 CREATE TABLE fundamental.statement_rejection(
  source_record_id BIGINT NOT NULL,rule_code VARCHAR NOT NULL,fields VARCHAR[] NOT NULL,
  PRIMARY KEY(source_record_id,rule_code));`)
	return err
}

// IndexedFinancialRecord points to the original package row, including when
// other rows in the same package repeat its code. Identity is resolved upstream.
type IndexedFinancialRecord struct {
	ID       int64
	Revision string
	Record   domain.ProviderFinancialRecord
}
