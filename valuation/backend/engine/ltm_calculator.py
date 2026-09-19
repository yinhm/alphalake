"""
LTM (Trailing Twelve Month) Calculator — Ginzu formula.

Per the Ginzu `Trailing 12 month` sheet (rows 2,3,4,5 → E = B − C + D):

  For flow items:  LTM = Last_10K − Prior_Year_YTD + Current_Year_YTD
  For balance sheet items: use most recent 10-Q point-in-time value (no rotation).

Where YTDs are aligned by calendar window: if K quarters have elapsed since the
last fiscal year end, Current_YTD = FQ-0..FQ-(K-1) and Prior_YTD = FQ-4..FQ-(K+3).
"""

from __future__ import annotations

from .data_dictionary import RawFinancials


FLOW_FIELDS = {
    "revenues", "ebit", "ebitda", "net_income", "interest_expense",
    "d_a", "capex", "r_and_d_expense", "earnings_before_tax", "total_tax_expense",
    "change_in_noncash_wc", "net_debt_issued",
}

BALANCE_SHEET_FIELDS = {
    "cash_and_marketable_securities", "bv_equity", "bv_debt",
    "noncash_wc", "shares_outstanding", "cross_holdings", "minority_interests",
    "mv_equity", "mv_debt", "stock_price",
}


def compute_ltm_financials(
    fy0: RawFinancials,
    quarterly: list[RawFinancials],
    quarters_since_10k: int,
) -> RawFinancials:
    """Build a RawFinancials with LTM-rotated flow values + FQ-0 balance-sheet snapshot.

    If quarters_since_10k == 0, returns FY0 unchanged.
    Insufficient quarters fail; missing optional flow components propagate as None.
    """
    if not 0 <= quarters_since_10k <= 4:
        raise ValueError("quarters_since_10k must be between 0 and 4")
    K = quarters_since_10k

    if K == 0:
        # No rotation needed — most recent 10-Q is the 10-K itself
        return fy0.model_copy()

    if len(quarterly) < K + 4:
        raise ValueError("insufficient quarterly data for LTM")
    data = fy0.model_dump()

    for field in FLOW_FIELDS:
        fy0_val = data.get(field)
        if fy0_val is None:
            continue
        values = [getattr(quarterly[i], field) for i in list(range(K)) + list(range(4, 4 + K))]
        if any(v is None for v in values):
            if field in ("revenues", "ebit"):
                raise ValueError(f"incomplete LTM field: {field}")
            data[field] = None
            continue
        current_sum = sum(values[:K])
        prior_sum = sum(values[K:])
        data[field] = fy0_val - prior_sum + current_sum

    # Balance sheet: FQ-0 only; never substitute stale annual balances.
    if quarterly:
        for field in BALANCE_SHEET_FIELDS:
            qv = getattr(quarterly[0], field, None)
            data[field] = qv

    return RawFinancials(**data)
