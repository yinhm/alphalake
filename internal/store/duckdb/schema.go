package duckdb

import (
	"context"
	"database/sql"
	_ "embed"
	"fmt"
	"strings"
)

const SchemaVersion = 54

//go:embed schema.sql
var schemaSQL string

//go:embed financial_queries.sql
var financialQueriesSQL string

//go:embed filing_coverage.sql
var filingCoverageSQL string

// Initialize creates the current schema atomically in an empty database.
// Existing current databases are left unchanged; older/newer versions are rejected.
func Initialize(ctx context.Context, db *sql.DB) error {
	if db == nil {
		return fmt.Errorf("duckdb is nil")
	}
	version, err := CurrentSchemaVersion(ctx, db)
	if err != nil {
		return err
	}
	if version == SchemaVersion {
		return nil
	}
	if version != 0 {
		return fmt.Errorf("unsupported schema %d; current schema is %d; preserve evidence and rebuild or use the original implementation", version, SchemaVersion)
	}
	var objects int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM duckdb_tables() WHERE database_name=current_database() AND NOT internal`).Scan(&objects); err != nil {
		return err
	}
	if objects != 0 {
		return fmt.Errorf("refuse initialization of a nonempty unversioned database")
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err := tx.ExecContext(ctx, schemaSQL); err != nil {
		return fmt.Errorf("initialize current schema: %w", err)
	}
	if _, err := tx.ExecContext(ctx, filingCoverageSQL); err != nil {
		return err
	}
	if err := insertSourceFieldCatalog(ctx, tx); err != nil {
		return err
	}
	if _, err := insertCapitalHistory(ctx, tx); err != nil {
		return err
	}
	fields, err := loadSnapshotFields(ctx, tx)
	if err != nil {
		return err
	}
	if err = CreateFinancialSnapshotTables(ctx, tx, fields); err != nil {
		return err
	}
	if err = installSnapshotQueries(ctx, tx, fields); err != nil {
		return err
	}
	return tx.Commit()
}

func CurrentSchemaVersion(ctx context.Context, db *sql.DB) (int, error) {
	if db == nil {
		return 0, fmt.Errorf("duckdb is nil")
	}
	var exists int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM information_schema.tables WHERE table_catalog=current_database() AND table_schema='meta' AND table_name='schema_version'`).Scan(&exists); err != nil {
		return 0, err
	}
	if exists == 0 {
		return 0, nil
	}
	var version int
	if err := db.QueryRowContext(ctx, `SELECT coalesce(max(version),0) FROM meta.schema_version`).Scan(&version); err != nil {
		return 0, err
	}
	return version, nil
}

// UpgradeFilingCoverage is an explicit, one-time schema52 -> schema53 change.
// It adds only disclosure review metadata; standard financial values are untouched.
func UpgradeFilingCoverage(ctx context.Context, db *sql.DB) error {
	version, err := CurrentSchemaVersion(ctx, db)
	if err != nil {
		return err
	}
	if version != 52 {
		return fmt.Errorf("coverage upgrade requires schema52, found %d", version)
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err = tx.ExecContext(ctx, filingCoverageSQL); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO meta.schema_version(version,description) VALUES (53,'Reviewed prospectus disclosure coverage')`); err != nil {
		return err
	}
	return tx.Commit()
}

// UpgradeNativeReferences changes reference tables only; financial values and evidence remain unchanged.
func UpgradeNativeReferences(ctx context.Context, db *sql.DB) error {
	version, err := CurrentSchemaVersion(ctx, db)
	if err != nil {
		return err
	}
	if version != 53 {
		return fmt.Errorf("native reference upgrade requires schema53, found %d", version)
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err = tx.ExecContext(ctx, `ALTER TABLE reference.industry_stat RENAME TO industry_stat_before_upgrade`); err != nil {
		return err
	}
	for _, statement := range strings.Split(schemaSQL, ";") {
		statement = strings.TrimSpace(statement)
		if strings.HasPrefix(statement, "CREATE TABLE reference.industry_stat(") || strings.HasPrefix(statement, "CREATE TABLE reference.country_tax(") {
			if _, err = tx.ExecContext(ctx, statement); err != nil {
				return err
			}
		}
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO reference.industry_stat SELECT * FROM reference.industry_stat_before_upgrade; DROP TABLE reference.industry_stat_before_upgrade; INSERT INTO meta.schema_version(version,description) VALUES (54,'Native valuation industry references and country tax')`); err != nil {
		return err
	}
	return tx.Commit()
}
