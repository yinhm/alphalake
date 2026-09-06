-- 两公司合并报表 2025H1/FY2025/2026H1 原文与 gpcw 位级证据见 earnings-working-capital-2026。
-- FN47 是全部应交税费，不能代替应交所得税；损益字段是累计流量，不是现金流量表调节项。
INSERT INTO fundamental.provider_field
    (source, provider_field, canonical_field, display_name, unit, value_kind, valid_from, period_basis, value_multiplier, notes)
VALUES
    ('tdx', 'FN12', 'prepayments', '预付款项', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 1, 'Verified consolidated statement amounts; earnings-working-capital-2026'),
    ('tdx', 'FN13', 'other_receivables', '其他应收款', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 1, 'Net carrying amount, not gross note components; earnings-working-capital-2026'),
    ('tdx', 'FN46', 'payroll_payable', '应付职工薪酬', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 1, 'Verified consolidated statement amounts; earnings-working-capital-2026'),
    ('tdx', 'FN47', 'taxes_payable', '应交税费', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 1, 'All taxes payable, not income tax payable; earnings-working-capital-2026'),
    ('tdx', 'FN82', 'fair_value_change_income', '公允价值变动收益', 'CNY', 'monetary', DATE '2025-01-01', 'ytd', 1, 'Income statement gain/loss, not cashflow reconciliation adjustment; earnings-working-capital-2026'),
    ('tdx', 'FN83', 'investment_income', '投资收益', 'CNY', 'monetary', DATE '2025-01-01', 'ytd', 1, 'Income statement gain/loss, not investment cash received; earnings-working-capital-2026'),
    ('tdx', 'FN301', 'asset_disposal_income', '资产处置收益', 'CNY', 'monetary', DATE '2025-01-01', 'ytd', 1, 'Income statement gain/loss, not gross asset disposal proceeds; earnings-working-capital-2026');
