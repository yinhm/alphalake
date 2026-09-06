-- 原文、单位、累计期间及正负号核对见 cash-rd-2026；源零值由物化规则保守拒绝。
INSERT INTO fundamental.provider_field
    (source, provider_field, canonical_field, display_name, unit, value_kind, valid_from, period_basis, value_multiplier, notes)
VALUES
    ('tdx', 'FN99', 'tax_refunds_received', '收到的税费返还', 'CNY', 'monetary', DATE '2025-01-01', 'ytd', 1, 'Cashflow statement; not income tax benefit; cash-rd-2026'),
    ('tdx', 'FN104', 'taxes_paid', '支付的各项税费', 'CNY', 'monetary', DATE '2025-01-01', 'ytd', 1, 'Cashflow statement; not operating income tax paid; cash-rd-2026'),
    ('tdx', 'FN146', 'inventory_decrease_cashflow', '存货的减少', 'CNY', 'monetary', DATE '2025-01-01', 'ytd', 1, 'Cashflow reconciliation, decrease positive/increase negative; cash-rd-2026'),
    ('tdx', 'FN147', 'operating_receivables_decrease_cashflow', '经营性应收项目的减少', 'CNY', 'monetary', DATE '2025-01-01', 'ytd', 1, 'Cashflow reconciliation; not balance sheet receivables delta; cash-rd-2026'),
    ('tdx', 'FN148', 'operating_payables_increase_cashflow', '经营性应付项目的增加', 'CNY', 'monetary', DATE '2025-01-01', 'ytd', 1, 'Cashflow reconciliation; not classified valuation working capital; cash-rd-2026'),
    ('tdx', 'FN304', 'research_and_development_expense', '研发费用', 'CNY', 'monetary', DATE '2025-01-01', 'ytd', 1, 'Income statement expense; not total R&D spending or capitalized development; cash-rd-2026');
