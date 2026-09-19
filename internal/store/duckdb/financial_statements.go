package duckdb

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"
)

// ExportFinancialStatements returns reported-period observations, not TTM or a
// reconstructed PDF. The SQL macro retains absent and unreviewed statement rows.
func ExportFinancialStatements(ctx context.Context, db *sql.DB, code string, period, asof time.Time, evidence bool) (map[string]any, error) {
	if !sixDigitCode.MatchString(code) || period.IsZero() || asof.IsZero() || period.AddDate(0, 0, 1).Day() != 1 || int(period.Month())%3 != 0 {
		return nil, errors.New("six-digit code, quarter-end report period and information cutoff are required")
	}
	if err := requireStandardFinancialSchema(ctx, db); err != nil {
		return nil, err
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	var raw string
	err = tx.QueryRowContext(ctx, `SELECT CAST(to_json(list(x ORDER BY statement,section,field)) AS VARCHAR) FROM (
 SELECT * EXCLUDE(value,source_evidence),CAST(value AS VARCHAR) AS value,source_evidence
 FROM fundamental.statements_asof(?,?,?,include_evidence := ?)) x`, asof, period, code, evidence).Scan(&raw)
	if err != nil {
		return nil, fmt.Errorf("query standard financial statements: %w", err)
	}
	var rows []map[string]any
	decoder := json.NewDecoder(strings.NewReader(raw))
	decoder.UseNumber()
	if err = decoder.Decode(&rows); err != nil {
		return nil, err
	}
	statements := map[string][]map[string]any{"balance_sheet": {}, "income_statement": {}, "cash_flow_statement": {}}
	counts := map[string]map[string]int{}
	identity := "unresolved_identity"
	for _, row := range rows {
		identity = row["identity_status"].(string)
		statement := row["statement"].(string)
		if !evidence {
			delete(row, "source_evidence")
		}
		// Shared request metadata belongs in the envelope, not on every line.
		for _, key := range []string{"identity_status", "code", "report_period", "information_as_of", "statement"} {
			delete(row, key)
		}
		statements[statement] = append(statements[statement], row)
		if counts[statement] == nil {
			counts[statement] = map[string]int{}
		}
		counts[statement][row["status"].(string)]++
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"contract_version": "alphalake-financial-statements-v1", "code": code, "report_period": period.Format("2006-01-02"), "information_as_of": asof.UTC().Format(time.RFC3339Nano), "identity_status": identity, "statement_scope": "provider_default", "scope": "standard_report_observations_not_individual_company_pdf_certification", "statements": statements, "coverage": counts}, nil
}
