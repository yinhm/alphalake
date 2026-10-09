package duckdb

import (
	"context"
	"database/sql"
	_ "embed"
	"fmt"
	"strings"
)

const SchemaVersion = 58

//go:embed earnings_guidance.sql
var earningsGuidanceSQL string

//go:embed schema.sql
var schemaSQL string

//go:embed financial_queries.sql
var financialQueriesSQL string

//go:embed filing_coverage.sql
var filingCoverageSQL string

//go:embed issuer_industry.sql
var issuerIndustrySQL string

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
	if _, err = tx.ExecContext(ctx, issuerIndustrySQL); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, earningsGuidanceSQL); err != nil {
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
	if version != 54 {
		return fmt.Errorf("native reference upgrade requires schema54, found %d", version)
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
		if strings.HasPrefix(statement, "CREATE TABLE reference.industry_stat(") {
			if _, err = tx.ExecContext(ctx, statement); err != nil {
				return err
			}
		}
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO reference.industry_stat SELECT * FROM reference.industry_stat_before_upgrade; DROP TABLE reference.industry_stat_before_upgrade; INSERT INTO meta.schema_version(version,description) VALUES (55,'Separate lease and research adjusted industry margins')`); err != nil {
		return err
	}
	return tx.Commit()
}

// UpgradeFinancialAvailability separates numeric facts from disclosure metadata.
// Call on an unpublished copy, then rebuild and validate before replacing the main database.
func UpgradeFinancialAvailability(ctx context.Context, db *sql.DB) error {
	version, err := CurrentSchemaVersion(ctx, db)
	if err != nil {
		return err
	}
	if version != 55 {
		return fmt.Errorf("financial availability upgrade requires schema55, found %d", version)
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err = tx.ExecContext(ctx, `ALTER TABLE fundamental.statement_snapshot ALTER COLUMN source_filing_id DROP NOT NULL;
 ALTER TABLE fundamental.statement_snapshot ALTER COLUMN announcement_time DROP NOT NULL;
 ALTER TABLE fundamental.statement_snapshot ADD COLUMN announcement_source VARCHAR DEFAULT 'cninfo';
 DELETE FROM fundamental.materialization_state;
 INSERT INTO meta.schema_version(version,description) VALUES(56,'TDX disclosure dates; financial values independent of announcement linkage')`); err != nil {
		return err
	}
	fields, err := loadSnapshotFields(ctx, tx)
	if err != nil {
		return err
	}
	queries, err := snapshotObservationQueries(fields)
	if err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, queries); err != nil {
		return err
	}
	return tx.Commit()
}

// UpgradeIssuerReferences adds reviewed reference associations without rewriting financial facts.
func UpgradeIssuerReferences(ctx context.Context, db *sql.DB) error {
	version, err := CurrentSchemaVersion(ctx, db)
	if err != nil {
		return err
	}
	if version != 56 {
		return fmt.Errorf("issuer reference upgrade requires schema56, found %d", version)
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err = tx.ExecContext(ctx, issuerIndustrySQL); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO meta.schema_version(version,description) VALUES (57,'Reviewed same-issuer reference associations')`); err != nil {
		return err
	}
	return tx.Commit()
}

// UpgradeEarningsGuidance is a one-time metadata addition on an unpublished copy.
func UpgradeEarningsGuidance(ctx context.Context, db *sql.DB) error {
	version, err := CurrentSchemaVersion(ctx, db)
	if err != nil {
		return err
	}
	if version != 57 {
		return fmt.Errorf("earnings guidance upgrade requires schema57, found %d", version)
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	before, err := tableContentSignatures(ctx, tx)
	if err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, earningsGuidanceSQL); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO meta.schema_version(version,description) VALUES (58,'Independent company earnings guidance')`); err != nil {
		return err
	}
	after, err := tableContentSignatures(ctx, tx)
	if err != nil {
		return err
	}
	for table, signature := range before {
		if after[table] != signature {
			return fmt.Errorf("guidance migration changed existing table %s", table)
		}
	}
	return tx.Commit()
}

// Aggregate inside DuckDB; never decode the existing financial matrix in Go.
func tableContentSignatures(ctx context.Context, tx *sql.Tx) (map[string]string, error) {
	rows, err := tx.QueryContext(ctx, `SELECT schema_name,table_name FROM duckdb_tables() WHERE database_name=current_database() AND NOT internal AND NOT (schema_name='meta' AND table_name='schema_version') ORDER BY schema_name,table_name`)
	if err != nil {
		return nil, err
	}
	names := [][2]string{}
	for rows.Next() {
		var schema, name string
		if err = rows.Scan(&schema, &name); err != nil {
			rows.Close()
			return nil, err
		}
		names = append(names, [2]string{schema, name})
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return nil, err
	}
	out := map[string]string{}
	for _, name := range names {
		qualified := `"` + strings.ReplaceAll(name[0], `"`, `""`) + `"."` + strings.ReplaceAll(name[1], `"`, `""`) + `"`
		var count int64
		var hash string
		if err = tx.QueryRowContext(ctx, `SELECT count(*),CAST(coalesce(bit_xor(hash(t)),0) AS VARCHAR) FROM `+qualified+` t`).Scan(&count, &hash); err != nil {
			return nil, fmt.Errorf("table fingerprint %s: %w", qualified, err)
		}
		out[qualified] = fmt.Sprintf("%d:%s", count, hash)
	}
	return out, nil
}
