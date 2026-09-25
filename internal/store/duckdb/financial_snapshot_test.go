package duckdb

import (
	"context"
	"database/sql"
	"database/sql/driver"
	"fmt"
	"math"
	"math/big"
	"math/rand"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	duckdbgo "github.com/duckdb/duckdb-go/v2"
	"github.com/yinhm/alphalake/internal/domain"
)

func TestWideSnapshotSemanticGateAndRollback(t *testing.T) {
	ctx := context.Background()
	db, err := OpenInitialized(ctx, filepath.Join(t.TempDir(), "wide.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	check := func(e error) {
		t.Helper()
		if e != nil {
			t.Fatal(e)
		}
	}
	fields, err := LoadSnapshotFields(ctx, db)
	check(err)
	tx, err := db.BeginTx(ctx, nil)
	check(err)

	check(tx.Commit())
	period := time.Date(2025, 12, 31, 0, 0, 0, 0, time.UTC)
	ann := period.AddDate(0, 3, 1)
	_, err = db.ExecContext(ctx, `INSERT INTO fundamental.filing(filing_id,instrument_id,source,source_filing_id,report_period,announcement_time,filing_type) VALUES(1,1,'cninfo','sample',?,?,'annual')`, period, ann)
	check(err)
	_, err = db.ExecContext(ctx, `INSERT INTO fundamental.provider_filing_link(provider_source,provider_revision_key,provider_artifact_id,provider_code,report_period,instrument_id,filing_id,status,linker_version) VALUES('tdx','revision',1,'300866',?,1,1,'linked','test')`, period)
	check(err)
	r := domain.ProviderFinancialRecord{InstrumentID: 1, Provider: "tdx", ProviderCode: "300866", ReportPeriod: period, ProviderFields: make([]domain.ProviderFloat32, 584)}
	r.ProviderFields[229] = domain.ProviderFloat32{Bits: math.Float32bits(123.5), Value: 123.5}
	r.ProviderFields[438] = domain.ProviderFloat32{Bits: math.Float32bits(309.80), Value: float64(float32(309.80))}
	conn, err := db.Conn(ctx)
	check(err)
	defer conn.Close()
	_, err = conn.ExecContext(ctx, "BEGIN")
	check(err)
	reference, err := materializeFinancialSnapshotSQLReference(ctx, conn, 1, fields, []IndexedFinancialRecord{{ID: 1, Revision: "revision", Record: r}})
	check(err)
	var expected string
	check(conn.QueryRowContext(ctx, `SELECT CAST(to_json(list(s)) AS VARCHAR) FROM fundamental.statement_snapshot s`).Scan(&expected))
	_, err = conn.ExecContext(ctx, "ROLLBACK; BEGIN")
	check(err)
	result, err := MaterializeFinancialSnapshotBatch(ctx, conn, 1, fields, []IndexedFinancialRecord{{ID: 1, Revision: "revision", Record: r}})
	check(err)
	var actual string
	check(conn.QueryRowContext(ctx, `SELECT CAST(to_json(list(s)) AS VARCHAR) FROM fundamental.statement_snapshot s`).Scan(&actual))
	if expected != actual || result.Candidates != reference.Candidates || result.Rejected != reference.Rejected {
		t.Fatal("wide values or rejection denominator differ from SQL gate", result, reference)
	}
	if result.Materialized < 2 || result.Rejected == 0 {
		t.Fatal(result)
	}
	var revenue, lease string
	check(conn.QueryRowContext(ctx, `SELECT CAST(revenue AS VARCHAR),CAST(lease_liabilities AS VARCHAR) FROM fundamental.statement_snapshot`).Scan(&revenue, &lease))
	if revenue != "123.5000000000" || lease != "3097999.8779296875" {
		t.Fatal(revenue, lease)
	}
	_, err = conn.ExecContext(ctx, "ROLLBACK")
	check(err)
	var n int
	check(conn.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.statement_snapshot`).Scan(&n))
	if n != 0 {
		t.Fatal("rollback left partial snapshot")
	}
	// A changed mapping cannot bypass the same semantic gate as current materialization.
	_, err = conn.ExecContext(ctx, `UPDATE fundamental.provider_field SET value_multiplier=-1 WHERE canonical_field='lease_liabilities'; BEGIN`)
	check(err)
	_, err = MaterializeFinancialSnapshotBatch(ctx, conn, 2, fields, []IndexedFinancialRecord{{ID: 1, Revision: "revision", Record: r}})
	check(err)
	check(conn.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.statement_snapshot WHERE lease_liabilities IS NOT NULL`).Scan(&n))
	if n != 0 {
		t.Fatal("invalid multiplier accepted")
	}
	check(conn.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.statement_rejection WHERE rule_code='canonical_scale_unknown' AND list_contains(fields,'lease_liabilities')`).Scan(&n))
	if n != 1 {
		t.Fatal("missing rejection")
	}
	_, err = conn.ExecContext(ctx, "COMMIT")
	check(err)
	// A malformed source label must not be interpreted merely by its numeric tail.
	_, err = conn.ExecContext(ctx, `UPDATE fundamental.provider_field SET provider_field='ZZ439',value_multiplier=10000 WHERE canonical_field='lease_liabilities'; BEGIN`)
	check(err)
	_, err = MaterializeFinancialSnapshotBatch(ctx, conn, 3, fields, []IndexedFinancialRecord{{ID: 2, Revision: "revision", Record: r}})
	if err == nil || !strings.Contains(err.Error(), "invalid source position") {
		t.Fatalf("malformed source label accepted: %v", err)
	}
	_, err = conn.ExecContext(ctx, "ROLLBACK")
	check(err)
}

// Compare fixed-point SQL text: DuckDB scientific-text casts can incorrectly
// round tiny exponents up to one scale unit. Real legacy cells are separately
// checked by check-financial-rebuild; we do not encode that parser defect.
func TestSnapshotDecimalMatchesSQL(t *testing.T) {
	ctx := context.Background()
	db, err := Open(ctx, filepath.Join(t.TempDir(), "decimal.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	stmt, err := db.PrepareContext(ctx, `SELECT CAST(try_cast(? AS DECIMAL(38,10)) AS VARCHAR)`)
	if err != nil {
		t.Fatal(err)
	}
	defer stmt.Close()
	random := rand.New(rand.NewSource(42))
	values := []float64{0, math.Copysign(0, -1), 0.00000000005, -0.00000000005, 0.00000000015, -0.00000000015, float64(float32(309.80)) * 10000, math.SmallestNonzeroFloat32, math.Nextafter(1e28, 0)}
	for i := 0; i < 2048; i++ {
		v := float64(math.Float32frombits(random.Uint32()))
		for _, m := range []float64{1, 10000} {
			if !math.IsNaN(v) && !math.IsInf(v, 0) && math.Abs(v*m) < 1e28 {
				values = append(values, v*m)
			}
		}
	}
	for _, v := range values {
		var expected string
		if err := stmt.QueryRowContext(ctx, strconv.FormatFloat(v, 'f', -1, 64)).Scan(&expected); err != nil {
			t.Fatal(v, err)
		}
		actual := snapshotDecimal(v).String()
		// Decimal.String omits insignificant trailing zeros; compare exact rationals.
		a, ok := new(big.Rat).SetString(actual)
		if !ok {
			t.Fatal(actual)
		}
		b, ok := new(big.Rat).SetString(expected)
		if !ok || a.Cmp(b) != 0 {
			t.Fatalf("value %.17g: got %s, SQL %s", v, actual, expected)
		}
	}
}

func TestSnapshotAsOfRanksIssuerBeforeCode(t *testing.T) {
	ctx := context.Background()
	db, err := OpenInitialized(ctx, filepath.Join(t.TempDir(), "query.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	_, err = db.ExecContext(ctx, `
 INSERT INTO meta.artifact(artifact_id,source,dataset,source_locator,fetched_at,sha256,content_length) VALUES
 (1,'tdx','professional_financial','old','2026-01-01','a',1),(2,'tdx','professional_financial','new','2026-04-01','b',1);
 INSERT INTO fundamental.source_record VALUES (1,1,1,'000001',0,584,'2025-12-31',1),(2,2,1,'000002',0,584,'2025-12-31',1);
 INSERT INTO fundamental.statement_snapshot(source_record_id,instrument_id,source_filing_id,report_period,announcement_time,ingest_run_id,revenue)
 VALUES (1,1,1,'2025-12-31','2026-03-01',1,10),(2,1,2,'2025-12-31','2026-04-01',2,20);`)
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		code, asof string
		count      int
		amount     string
	}{
		{"000001", "2026-03-31", 1, "10.0000000000"},
		{"000001", "2026-04-01", 0, ""},
		{"000002", "2026-04-01", 1, "20.0000000000"},
	} {
		var n int
		var amount string
		err = db.QueryRowContext(ctx, `SELECT count(*),coalesce(CAST(sum(value) AS VARCHAR),'') FROM fundamental.financial_observations_asof(?,NULL,NULL,?)`, tc.code, tc.asof).Scan(&n, &amount)
		if err != nil {
			t.Fatal(err)
		}
		if n != tc.count || amount != tc.amount {
			t.Fatalf("%+v: got %d %s", tc, n, amount)
		}
	}
}

// stageFinancialSourceReference expands only reviewed positions for a bounded batch.
// Unknown positions remain intact in the original ZIP, without database copies.
func stageFinancialSourceReference(ctx context.Context, conn *sql.Conn, records []IndexedFinancialRecord) error {
	positions := map[int]bool{}
	rows, err := conn.QueryContext(ctx, `SELECT DISTINCT try_cast(substr(provider_field,3) AS INTEGER) FROM fundamental.provider_field WHERE source='tdx' AND canonical_field IS NOT NULL`)
	if err != nil {
		return err
	}
	for rows.Next() {
		var n sql.NullInt64
		if err = rows.Scan(&n); err != nil {
			rows.Close()
			return err
		}
		if !n.Valid || n.Int64 < 1 || n.Int64 > 4096 {
			rows.Close()
			return fmt.Errorf("invalid source mapping position")
		}
		positions[int(n.Int64)] = true
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	if _, err = conn.ExecContext(ctx, `CREATE OR REPLACE TEMP TABLE _provider_fields(provider_fact_id BIGINT,instrument_id BIGINT,source VARCHAR,revision_key VARCHAR,provider_code VARCHAR,provider_field VARCHAR,report_period DATE,value DOUBLE)`); err != nil {
		return err
	}
	return conn.Raw(func(raw any) error {
		app, e := duckdbgo.NewAppender(raw.(driver.Conn), "temp", "main", "_provider_fields")
		if e != nil {
			return e
		}
		done := false
		defer func() {
			if !done {
				_ = app.Clear()
				_ = app.Close()
			}
		}()
		for _, r := range records {
			if r.ID <= 0 || r.ID > math.MaxInt64/8192 || r.Record.InstrumentID <= 0 {
				return fmt.Errorf("invalid source record identity")
			}
			for i, v := range r.Record.ProviderFields {
				if !positions[i+1] {
					continue
				}
				if e = app.AppendRow(r.ID*8192+int64(i+1), r.Record.InstrumentID, "tdx", r.Revision, r.Record.ProviderCode, fmt.Sprintf("FN%d", i+1), r.Record.ReportPeriod, v.Value); e != nil {
					return e
				}
			}
		}
		if e = app.CloseWithCancel(ctx); e != nil {
			return e
		}
		done = true
		return nil
	})
}

// MaterializeFinancialSnapshotBatch reuses the audited SQL semantic gate, then
// writes only wide standard values and grouped rejection metadata.
func materializeFinancialSnapshotSQLReference(ctx context.Context, conn *sql.Conn, runID int64, fields []SnapshotField, records []IndexedFinancialRecord) (CanonicalFundamentalResult, error) {
	var result CanonicalFundamentalResult
	if err := stageFinancialSourceReference(ctx, conn, records); err != nil {
		return result, err
	}
	defer func() {
		for _, t := range []string{"_provider_fields", fundamentalFactStage, fundamentalRejectStage} {
			_, _ = conn.ExecContext(context.WithoutCancel(ctx), `DROP TABLE IF EXISTS temp.main.`+t)
		}
	}()
	result, err := stageCanonicalFundamentals(ctx, conn, runID, "tdx", "", "temp.main._provider_fields")
	if err != nil {
		return result, err
	}
	var duplicate bool
	if err = conn.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM temp.main.`+fundamentalFactStage+` GROUP BY provider_fact_id // 8192,canonical_field HAVING count(*)>1)`).Scan(&duplicate); err != nil {
		return result, err
	}
	if duplicate {
		return result, fmt.Errorf("multiple source positions map to one standard cell")
	}
	columns := make([]string, len(fields))
	for i, f := range fields {
		columns[i] = duckdbStringLiteral(f.Name)
	}
	_, err = conn.ExecContext(ctx, `INSERT INTO fundamental.statement_snapshot BY NAME
 SELECT * FROM (PIVOT (
 SELECT provider_fact_id // 8192 AS source_record_id,instrument_id,filing_id AS source_filing_id,report_period,announcement_time,ingest_run_id,canonical_field,value
 FROM temp.main.`+fundamentalFactStage+`) ON canonical_field IN (`+strings.Join(columns, ",")+`) USING first(value))`)
	if err != nil {
		return result, err
	}
	_, err = conn.ExecContext(ctx, `INSERT INTO fundamental.statement_rejection
 SELECT provider_fact_id // 8192,rejection_rule,list(canonical_field ORDER BY canonical_field)
 FROM temp.main.`+fundamentalRejectStage+` WHERE rejection_rule IS NOT NULL GROUP BY 1,2`)
	return result, err
}

func TestFinancialRebuildRejectsCorruptArchive(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	previous := filepath.Join(root, "previous.duckdb")
	output := filepath.Join(root, "candidate.duckdb")
	db, err := OpenInitialized(ctx, previous)
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(root, "gpcw20251231.zip"), []byte("corrupt"), 0600); err != nil {
		t.Fatal(err)
	}
	_, err = db.ExecContext(ctx, `DELETE FROM meta.schema_version; INSERT INTO meta.schema_version(version,description) VALUES(51,'test legacy source'); CREATE TABLE fundamental.fact(primary_source VARCHAR);
 INSERT INTO meta.artifact(artifact_id,source,dataset,source_locator,fetched_at,sha256,content_length,local_path) VALUES (1,'tdx','professional_financial','gpcw20251231.zip',now(),'incorrect',7,'gpcw20251231.zip');
 INSERT INTO fundamental.provider_record_resolution(artifact_id,source,source_file,report_period,provider_code,market_marker,status,instrument_id) VALUES (1,'tdx','gpcw20251231.zip','2025-12-31','300866',0,'resolved',1);`)
	if err != nil {
		t.Fatal(err)
	}
	if err = db.Close(); err != nil {
		t.Fatal(err)
	}
	_, err = RebuildFinancialStorage(ctx, previous, output, root, filepath.Join(root, "audit"))
	if err == nil || !strings.Contains(err.Error(), "archive integrity failure") {
		t.Fatalf("wanted integrity refusal, got %v", err)
	}
	db, err = Open(ctx, output)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	var status string
	if err = db.QueryRowContext(ctx, `SELECT status FROM meta.ingest_run WHERE dataset='financial_storage_rebuild'`).Scan(&status); err != nil {
		t.Fatal(err)
	}
	if status != IngestRunFailed {
		t.Fatal(status)
	}
	if err = Initialize(ctx, db); err == nil {
		t.Fatal("failed candidate accepted by current runtime")
	}
	var n int
	if err = db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.statement_snapshot`).Scan(&n); err != nil || n != 0 {
		t.Fatal(n, err)
	}
	original, err := OpenReadOnly(ctx, previous)
	if err != nil {
		t.Fatal(err)
	}
	defer original.Close()
	version, err := CurrentSchemaVersion(ctx, original)
	if err != nil || version != 51 {
		t.Fatal(version, err)
	}
	if _, err = RebuildFinancialStorage(ctx, previous, output, root, filepath.Join(root, "other-audit")); err == nil {
		t.Fatal("existing output overwritten")
	}
}
