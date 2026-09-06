-- FN439/FN581 原始单位为万元，先保留源精度，再换算为人民币元。
ALTER TABLE fundamental.provider_field ADD COLUMN value_multiplier INTEGER DEFAULT 1;

INSERT INTO fundamental.provider_field
    (source, provider_field, canonical_field, display_name, unit, value_kind, valid_from, period_basis, value_multiplier, notes)
VALUES
    ('tdx', 'FN56', 'bonds_payable', '应付债券', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 1, 'Verified against consolidated CNINFO 2025 reports; see tax-debt-2025 samples'),
    ('tdx', 'FN80', 'finance_costs', '财务费用', 'CNY', 'monetary', DATE '2025-01-01', 'ytd', 1, 'Verified against consolidated CNINFO 2025 reports; see tax-debt-2025 samples'),
    ('tdx', 'FN86', 'operating_profit_cumulative', '累计营业利润', 'CNY', 'monetary', DATE '2025-01-01', 'ytd', 1, 'Verified against consolidated CNINFO 2025 reports; see tax-debt-2025 samples'),
    ('tdx', 'FN92', 'profit_before_tax', '利润总额', 'CNY', 'monetary', DATE '2025-01-01', 'ytd', 1, 'Verified against consolidated CNINFO 2025 reports; see tax-debt-2025 samples'),
    ('tdx', 'FN93', 'income_tax_expense', '所得税费用', 'CNY', 'monetary', DATE '2025-01-01', 'ytd', 1, 'Verified against consolidated CNINFO 2025 reports; see tax-debt-2025 samples'),
    ('tdx', 'FN305', 'interest_expense', '利息费用', 'CNY', 'monetary', DATE '2025-01-01', 'ytd', 1, 'Verified against consolidated CNINFO 2025 reports; see tax-debt-2025 samples'),
    ('tdx', 'FN306', 'interest_income', '利息收入', 'CNY', 'monetary', DATE '2025-01-01', 'ytd', 1, 'Verified against consolidated CNINFO 2025 reports; see tax-debt-2025 samples'),
    ('tdx', 'FN439', 'lease_liabilities', '租赁负债', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 10000, 'Verified against consolidated CNINFO 2025 reports; see tax-debt-2025 samples'),
    ('tdx', 'FN581', 'right_of_use_depreciation', '使用权资产折旧', 'CNY', 'monetary', DATE '2025-01-01', 'ytd', 10000, 'Verified against consolidated CNINFO 2025 reports; see tax-debt-2025 samples');
