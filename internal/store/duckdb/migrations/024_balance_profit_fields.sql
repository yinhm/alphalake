-- mootdx/QUANTAXIS 目录交叉参考，财报原文及源位证据见 balance-profit-2026。
INSERT INTO fundamental.provider_field
    (source, provider_field, canonical_field, display_name, unit, value_kind, valid_from, period_basis, value_multiplier, notes)
VALUES
    ('tdx', 'FN19', 'noncurrent_assets_due_within_one_year', '一年内到期的非流动资产', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 1, 'Total current portion of noncurrent assets; financial/nonfinancial classification is separate; balance-profit-2026'),
    ('tdx', 'FN20', 'other_current_assets', '其他流动资产', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 1, 'Mixed balance; not entirely operating working capital; balance-profit-2026'),
    ('tdx', 'FN27', 'fixed_assets_net', '固定资产', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 1, 'Net carrying amount, not gross original cost or cash capital expenditure; balance-profit-2026'),
    ('tdx', 'FN28', 'construction_in_progress', '在建工程', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 1, 'Balance, not cash capital expenditure; balance-profit-2026'),
    ('tdx', 'FN33', 'intangible_assets_net', '无形资产', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 1, 'Net carrying amount, not capitalized research history; balance-profit-2026'),
    ('tdx', 'FN37', 'deferred_tax_assets', '递延所得税资产', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 1, 'Balance, not cash tax payment or cashflow reconciliation decrease; balance-profit-2026'),
    ('tdx', 'FN50', 'other_payables', '其他应付款', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 1, 'Includes mixed operating/nonoperating items; not all valuation working capital; balance-profit-2026'),
    ('tdx', 'FN53', 'other_current_liabilities', '其他流动负债', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 1, 'Mixed liabilities; not all valuation working capital; balance-profit-2026'),
    ('tdx', 'FN60', 'deferred_tax_liabilities', '递延所得税负债', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 1, 'Balance, not cash tax payment or cashflow reconciliation increase; balance-profit-2026'),
    ('tdx', 'FN95', 'net_income_ytd', '净利润（累计）', 'CNY', 'monetary', DATE '2025-01-01', 'ytd', 1, 'Consolidated income statement YTD; not parent-only FN232 single quarter or FN134 cashflow adjustment; balance-profit-2026'),
    ('tdx', 'FN96', 'net_income_parent_ytd', '归属于母公司所有者的净利润（累计）', 'CNY', 'monetary', DATE '2025-01-01', 'ytd', 1, 'Parent income YTD; distinct from FN232 single quarter; balance-profit-2026'),
    ('tdx', 'FN97', 'net_income_minority_ytd', '少数股东损益（累计）', 'CNY', 'monetary', DATE '2025-01-01', 'ytd', 1, 'Minority profit/loss YTD; not minority book equity or segment allocation; balance-profit-2026');
