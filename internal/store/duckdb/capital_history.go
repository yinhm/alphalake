package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
)

// This review extends source definitions, not company valuation classifications.
// Existing dated definitions are retained; approved main-statement zeros share one field policy.
const capitalHistoryReview = `
 UPDATE fundamental.provider_field SET zero_policy='allow'
 WHERE source='tdx' AND canonical_field IN (
 'accounts_payable','accounts_receivable','current_assets','current_liabilities','inventories',
 'trading_financial_assets','equity_parent','bonds_payable','cash_and_cash_equivalents',
 'current_portion_noncurrent_liabilities','income_tax_expense','lease_liabilities',
 'long_term_borrowings','long_term_equity_investments','monetary_funds','profit_before_tax',
 'short_term_borrowings','total_equity','debt_investments','other_debt_investments',
 'other_noncurrent_financial_assets','research_and_development_expense','revenue_cumulative');
 UPDATE fundamental.provider_field SET notes=replace(notes,'; nonzero only',
 '; source zeros accepted 20261001; model suitability is separate')
 WHERE source='tdx' AND canonical_field='revenue_cumulative';
 CREATE TEMP TABLE _capital_history_review AS
 SELECT p.* REPLACE(DATE '1900-01-01' AS valid_from,p.valid_from AS valid_to,
 'official-capital-history-v1;'||s.definition_reference AS notes)
 FROM fundamental.provider_field p JOIN fundamental.source_field s
 ON s.source=p.source AND s.provider_field=p.provider_field AND s.name=p.canonical_field
 AND s.unit=p.unit AND s.value_kind=p.value_kind AND s.period_basis=p.period_basis
 AND s.value_multiplier=p.value_multiplier AND s.definition_status='official'
 WHERE p.source='tdx' AND p.valid_from=DATE '2025-01-01' AND p.valid_to IS NULL
 AND p.canonical_field IN (
 'accounts_payable','accounts_receivable','current_assets','current_liabilities','inventories','trading_financial_assets',
 'equity_parent','bonds_payable','cash_and_cash_equivalents','current_portion_noncurrent_liabilities',
 'deferred_expense_amortization','depreciation_depletion','income_tax_expense','intangible_amortization',
 'inventory_decrease_cashflow','investment_property_depreciation_amortization','lease_liabilities',
 'long_term_borrowings','long_term_equity_investments','monetary_funds',
 'operating_payables_increase_cashflow','operating_receivables_decrease_cashflow',
 'profit_before_tax','right_of_use_depreciation','short_term_borrowings','total_equity','noncontrolling_interests');`

func insertCapitalHistory(ctx context.Context, tx *sql.Tx) (int64, error) {
	if _, err := tx.ExecContext(ctx, capitalHistoryReview); err != nil {
		return 0, err
	}
	var count int
	if err := tx.QueryRowContext(ctx, `SELECT count(*) FROM _capital_history_review`).Scan(&count); err != nil {
		return 0, err
	}
	if count != 27 {
		return 0, fmt.Errorf("capital history requires 27 unchanged official definitions, found %d", count)
	}
	if err := tx.QueryRowContext(ctx, `SELECT count(*) FROM (
 SELECT p.* FROM fundamental.provider_field p JOIN _capital_history_review h
 ON p.source=h.source AND p.provider_field=h.provider_field
 WHERE p.valid_from<h.valid_to AND (p.valid_to IS NULL OR p.valid_to>h.valid_from)
 EXCEPT SELECT * FROM _capital_history_review)`).Scan(&count); err != nil {
		return 0, err
	}
	if count != 0 {
		return 0, fmt.Errorf("capital history overlaps a different existing review")
	}
	r, err := tx.ExecContext(ctx, `INSERT INTO fundamental.provider_field
 SELECT * FROM _capital_history_review EXCEPT SELECT * FROM fundamental.provider_field`)
	if err != nil {
		return 0, err
	}
	n, err := r.RowsAffected()
	if err != nil {
		return 0, err
	}
	_, err = tx.ExecContext(ctx, `DROP TABLE _capital_history_review`)
	return n, err
}

// ExtendCapitalHistory applies an explicit catalogue review to the current schema.
// The normal materializer detects the changed catalogue signature and rebuilds
// supported values from archived source records. No source values are copied here.
func ExtendCapitalHistory(ctx context.Context, db *sql.DB) (int64, error) {
	if err := requireStandardFinancialSchema(ctx, db); err != nil {
		return 0, err
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback()
	n, err := insertCapitalHistory(ctx, tx)
	if err != nil {
		return 0, err
	}
	// Keep published statement_field unchanged until the materializer commits
	// all affected values and their catalogue together.
	return n, tx.Commit()
}

// RebuildCapitalHistoryCopy consumes existing identities and filing links in a
// newly created, unpublished copy. Partial package commits are never a published
// result: the caller must verify the complete file before atomic replacement.
func RebuildCapitalHistoryCopy(ctx context.Context, db *sql.DB) (runID int64, result CanonicalFundamentalResult, retErr error) {
	if _, retErr = ExtendCapitalHistory(ctx, db); retErr != nil {
		return
	}
	runID, retErr = StartIngestRun(ctx, db, "alphalake", "capital_history_rebuild", nil)
	if retErr != nil {
		return
	}
	defer func() {
		status := IngestRunCompleted
		if result.Rejected > 0 {
			status = IngestRunPartial
		}
		if retErr != nil {
			status = IngestRunFailed
		}
		if errors.Is(retErr, context.Canceled) || errors.Is(retErr, context.DeadlineExceeded) {
			status = IngestRunCanceled
		}
		retErr = errors.Join(retErr, FinishIngestRun(context.WithoutCancel(ctx), db, runID, status, nil, retErr))
	}()
	result, retErr = materializeCanonicalFundamentals(ctx, db, runID, "tdx", true)
	return
}
