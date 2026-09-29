package duckdb

import (
	"database/sql"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	"github.com/yinhm/alphalake/internal/source/tdx/financial"
)

func TestCapitalHistoryReview(t *testing.T) {
	ctx := t.Context()
	db, err := OpenInitialized(ctx, filepath.Join(t.TempDir(), "history.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	check := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	var count int
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.provider_field WHERE notes LIKE 'official-capital-history-v1;%' AND valid_to=DATE '2025-01-01' AND zero_policy='reject'`).Scan(&count))
	if count != 8 {
		t.Fatal(count)
	}
	// Upgrade old historical policies in place; cashflow supplements remain rejected.
	checkExec := func(query string) { _, e := db.ExecContext(ctx, query); check(e) }
	checkExec(`UPDATE fundamental.provider_field SET zero_policy='reject' WHERE canonical_field IN ('bonds_payable','short_term_borrowings','long_term_borrowings','lease_liabilities') AND valid_to=DATE '2025-01-01'`)
	_, err = ExtendCapitalHistory(ctx, db)
	check(err)
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.provider_field WHERE canonical_field IN ('bonds_payable','short_term_borrowings','long_term_borrowings','lease_liabilities','research_and_development_expense','debt_investments') AND zero_policy!='allow'`).Scan(&count))
	if count != 0 {
		t.Fatal("approved field zero still rejected", count)
	}
	var before, after string
	original := `SELECT CAST(to_json(list(p ORDER BY provider_field)) AS VARCHAR) FROM fundamental.provider_field p WHERE valid_from=DATE '2025-01-01'`
	check(db.QueryRowContext(ctx, original).Scan(&before))
	_, err = db.ExecContext(ctx, `DELETE FROM fundamental.provider_field WHERE notes LIKE 'official-capital-history-v1;%'; DELETE FROM fundamental.statement_field WHERE notes LIKE 'official-capital-history-v1;%'`)
	check(err)
	n, err := ExtendCapitalHistory(ctx, db)
	check(err)
	if n != 26 {
		t.Fatal(n)
	}
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.statement_field WHERE notes LIKE 'official-capital-history-v1;%'`).Scan(&count))
	if count != 0 {
		t.Fatal("published semantics changed before atomic replay")
	}
	n, err = ExtendCapitalHistory(ctx, db)
	check(err)
	if n != 0 {
		t.Fatal("replay changed mappings", n)
	}
	check(db.QueryRowContext(ctx, original).Scan(&after))
	if before != after {
		t.Fatal("existing reviews changed")
	}
	// The current 25-field historical review lacks parent equity, not total equity.
	_, err = db.ExecContext(ctx, `CREATE TEMP TABLE previous_equity_review AS SELECT * FROM fundamental.provider_field WHERE (canonical_field='equity_parent' AND valid_to=DATE '2025-01-01') IS NOT TRUE; DELETE FROM fundamental.provider_field WHERE canonical_field='equity_parent' AND valid_to=DATE '2025-01-01'`)
	check(err)
	n, err = ExtendCapitalHistory(ctx, db)
	check(err)
	if n != 1 {
		t.Fatal("parent equity incremental review", n)
	}
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM (SELECT * FROM previous_equity_review EXCEPT SELECT * FROM fundamental.provider_field)`).Scan(&count))
	if count != 0 {
		t.Fatal("parent equity review changed previous mappings", count)
	}
	// Upgrading the earlier balance-field review adds only the six balance fields.
	_, err = db.ExecContext(ctx, `CREATE TEMP TABLE previous_review AS SELECT * FROM fundamental.provider_field WHERE valid_to=DATE '2025-01-01' AND canonical_field NOT IN ('accounts_payable','accounts_receivable','current_assets','current_liabilities','inventories','trading_financial_assets'); DELETE FROM fundamental.provider_field WHERE valid_to=DATE '2025-01-01' AND canonical_field IN ('accounts_payable','accounts_receivable','current_assets','current_liabilities','inventories','trading_financial_assets')`)
	check(err)
	n, err = ExtendCapitalHistory(ctx, db)
	check(err)
	if n != 6 {
		t.Fatal("incremental review", n)
	}
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM (SELECT * FROM previous_review EXCEPT SELECT * FROM fundamental.provider_field)`).Scan(&count))
	if count != 0 {
		t.Fatal("previous historical reviews changed", count)
	}
	// An incompatible historical review is never silently overwritten.
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET value_multiplier=1 WHERE canonical_field='lease_liabilities' AND valid_to=DATE '2025-01-01'`)
	check(err)
	if _, err = ExtendCapitalHistory(ctx, db); err == nil {
		t.Fatal("conflicting review accepted")
	}
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET value_multiplier=10000 WHERE canonical_field='lease_liabilities' AND valid_to=DATE '2025-01-01'; UPDATE fundamental.source_field SET value_multiplier=1 WHERE name='lease_liabilities'`)
	check(err)
	if _, err = ExtendCapitalHistory(ctx, db); err == nil {
		t.Fatal("incompatible official unit accepted")
	}
}

// The archived record verifies the historical interval, scale and zero gate.
// It is a source replay, not an independent PDF or valuation classification.
func TestCapitalHistoryArchivedRecord(t *testing.T) {
	ctx := t.Context()
	raw, err := os.ReadFile("../../ingest/testdata/anker-cash-history-2024/gpcw20241231.zip")
	if err != nil {
		t.Fatal(err)
	}
	pkg, err := financial.ParsePackage("gpcw20241231.zip", raw)
	if err != nil {
		t.Fatal(err)
	}
	if len(pkg.Records) != 1 || pkg.Records[0].Code != "300866" {
		t.Fatal("unexpected fixture")
	}
	source := pkg.Records[0]
	db, err := OpenInitialized(ctx, filepath.Join(t.TempDir(), "archive.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	check := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	fields, err := LoadSnapshotFields(ctx, db)
	check(err)
	_, err = db.ExecContext(ctx, `INSERT INTO fundamental.filing(filing_id,instrument_id,source,source_filing_id,report_period,announcement_time,filing_type) VALUES(1,1,'cninfo','test',?,'2025-04-29T16:00:00Z','annual');`, source.ReportPeriod)
	check(err)
	_, err = db.ExecContext(ctx, `INSERT INTO fundamental.provider_filing_link(provider_source,provider_revision_key,provider_artifact_id,provider_code,report_period,instrument_id,filing_id,status,linker_version) VALUES('tdx','revision',1,'300866',?,1,1,'linked','test')`, source.ReportPeriod)
	check(err)
	conn, err := db.Conn(ctx)
	check(err)
	defer conn.Close()
	r := domain.ProviderFinancialRecord{InstrumentID: 1, Provider: "tdx", ProviderCode: source.Code, ReportPeriod: source.ReportPeriod, ProviderFields: source.Fields}
	_, err = conn.ExecContext(ctx, "BEGIN")
	check(err)
	_, err = MaterializeFinancialSnapshotBatch(ctx, conn, 1, fields, []IndexedFinancialRecord{{ID: 1, Revision: "revision", Record: r}})
	check(err)
	var lease, depreciation float64
	var bonds sql.NullFloat64
	check(conn.QueryRowContext(ctx, `SELECT lease_liabilities,depreciation_depletion,bonds_payable FROM fundamental.statement_snapshot`).Scan(&lease, &depreciation, &bonds))
	if lease != 62898500.9765625 || depreciation != 42345596 || !bonds.Valid || bonds.Float64 != 0 {
		t.Fatal(lease, depreciation, bonds)
	}
	missing := r
	missing.ProviderFields = r.ProviderFields[:55]
	_, err = MaterializeFinancialSnapshotBatch(ctx, conn, 2, fields, []IndexedFinancialRecord{{ID: 1, Revision: "revision", Record: missing}})
	check(err)
	check(conn.QueryRowContext(ctx, `SELECT bonds_payable FROM fundamental.statement_snapshot`).Scan(&bonds))
	if bonds.Valid {
		t.Fatal("absent field became zero")
	}
	_, err = MaterializeFinancialSnapshotBatch(ctx, conn, 3, fields, []IndexedFinancialRecord{{ID: 1, Revision: "revision", Record: r}})
	check(err)
	// Synthetic zero mutations on a real record exercise the published field rules.
	zeroed := r
	zeroed.ProviderFields = append([]domain.ProviderFloat32(nil), r.ProviderFields...)
	catalog, err := financial.FieldCatalog()
	check(err)
	for _, field := range catalog {
		switch field.Name {
		case "short_term_borrowings", "long_term_borrowings", "lease_liabilities", "research_and_development_expense", "right_of_use_depreciation":
			zeroed.ProviderFields[field.Index-1] = domain.ProviderFloat32{}
		}
	}
	_, err = MaterializeFinancialSnapshotBatch(ctx, conn, 4, fields, []IndexedFinancialRecord{{ID: 1, Revision: "revision", Record: zeroed}})
	check(err)
	var approved int
	check(conn.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.statement_snapshot WHERE short_term_borrowings=0 AND long_term_borrowings=0 AND lease_liabilities=0 AND research_and_development_expense=0 AND right_of_use_depreciation IS NULL`).Scan(&approved))
	if approved != 1 {
		t.Fatal("main statement zeros or supplement exclusion incorrect")
	}
	_, err = MaterializeFinancialSnapshotBatch(ctx, conn, 5, fields, []IndexedFinancialRecord{{ID: 1, Revision: "revision", Record: r}})
	check(err)
	// Independently decoded float32 amounts from the archived 2024 annual ZIP.
	var balances [6]float64
	check(conn.QueryRowContext(ctx, `SELECT current_assets,current_liabilities,accounts_receivable,inventories,accounts_payable,trading_financial_assets FROM fundamental.statement_snapshot`).Scan(&balances[0], &balances[1], &balances[2], &balances[3], &balances[4], &balances[5]))
	if balances != [6]float64{12367549440, 5901511680, 1654200064, 3233554176, 1778359168, 2330707712} {
		t.Fatal("historical balances", balances)
	}
	var parentEquity, totalEquity float64
	check(conn.QueryRowContext(ctx, `SELECT equity_parent,total_equity FROM fundamental.statement_snapshot`).Scan(&parentEquity, &totalEquity))
	if parentEquity != 8958043136 || totalEquity != 9144519680 {
		t.Fatal("parent equity must not use total equity", parentEquity, totalEquity)
	}
	// Withdrawing the historical approval removes only the unsupported field.
	_, err = conn.ExecContext(ctx, `DELETE FROM fundamental.provider_field WHERE canonical_field='equity_parent' AND valid_to=DATE '2025-01-01'`)
	check(err)
	_, err = MaterializeFinancialSnapshotBatch(ctx, conn, 2, fields, []IndexedFinancialRecord{{ID: 1, Revision: "revision", Record: r}})
	check(err)
	var withdrawn sql.NullFloat64
	check(conn.QueryRowContext(ctx, `SELECT equity_parent,total_equity FROM fundamental.statement_snapshot`).Scan(&withdrawn, &totalEquity))
	if withdrawn.Valid || totalEquity != 9144519680 {
		t.Fatal("withdrawal changed total equity or retained unsupported parent equity", withdrawn, totalEquity)
	}
	// A corrupted multiplier must withdraw a previously supported value.
	_, err = conn.ExecContext(ctx, `UPDATE fundamental.provider_field SET value_multiplier=-1 WHERE canonical_field='lease_liabilities' AND valid_to=DATE '2025-01-01'`)
	check(err)
	_, err = MaterializeFinancialSnapshotBatch(ctx, conn, 2, fields, []IndexedFinancialRecord{{ID: 1, Revision: "revision", Record: r}})
	check(err)
	var rejected sql.NullFloat64
	check(conn.QueryRowContext(ctx, `SELECT lease_liabilities FROM fundamental.statement_snapshot`).Scan(&rejected))
	if rejected.Valid {
		t.Fatal("corrupt scale retained value")
	}
	_, err = conn.ExecContext(ctx, "ROLLBACK")
	check(err)
}

func TestCapitalHistoryCopyFailureIsRecorded(t *testing.T) {
	ctx := t.Context()
	db, err := OpenInitialized(ctx, filepath.Join(t.TempDir(), "copy.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	_, err = db.ExecContext(ctx, `DELETE FROM fundamental.provider_field WHERE notes LIKE 'official-capital-history-v1;%'; DELETE FROM fundamental.statement_field WHERE notes LIKE 'official-capital-history-v1;%'`)
	if err != nil {
		t.Fatal(err)
	}
	period := time.Date(2024, 12, 31, 0, 0, 0, 0, time.UTC)
	record, hash, path := archiveFinancialFixture(t, ctx, db, "300866", 1, period, map[int]float32{439: 310})
	if _, err = ReconcileFinancialSourceRecords(ctx, db, 1, "tdx", hash, []domain.ProviderFinancialRecord{record}); err != nil {
		t.Fatal(err)
	}
	if err = os.Rename(path, path+".held"); err != nil {
		t.Fatal(err)
	}
	run, _, err := RebuildCapitalHistoryCopy(ctx, db)
	if err == nil {
		t.Fatal("missing archive accepted")
	}
	var status, message string
	if err = db.QueryRowContext(ctx, `SELECT status,error_message FROM meta.ingest_run WHERE ingest_run_id=?`, run).Scan(&status, &message); err != nil || status != IngestRunFailed || message == "" {
		t.Fatal(status, message, err)
	}
	var count int
	if err = db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.statement_field WHERE notes LIKE 'official-capital-history-v1;%'`).Scan(&count); err != nil || count != 0 {
		t.Fatal("failed candidate published catalogue", count, err)
	}
}
