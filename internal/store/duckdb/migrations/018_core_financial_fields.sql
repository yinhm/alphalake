-- 原始 gpcw 金额单位为元；真实 2025 H1/Q3 财报对账见 core-financial-2025 样本。
-- report 保持已有映射的期间约定；instant 是期末存量，ytd 是年初至报告期末。
ALTER TABLE fundamental.provider_field ADD COLUMN period_basis VARCHAR DEFAULT 'report';

INSERT INTO fundamental.provider_field
    (source, provider_field, canonical_field, display_name, unit, value_kind, valid_from, period_basis, notes)
VALUES
    ('tdx', 'FN8', 'monetary_funds', '货币资金', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3'),
    ('tdx', 'FN11', 'accounts_receivable', '应收账款', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3'),
    ('tdx', 'FN17', 'inventories', '存货', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3'),
    ('tdx', 'FN21', 'current_assets', '流动资产合计', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3'),
    ('tdx', 'FN25', 'long_term_equity_investments', '长期股权投资', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3'),
    ('tdx', 'FN40', 'total_assets', '资产总计', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3'),
    ('tdx', 'FN41', 'short_term_borrowings', '短期借款', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3'),
    ('tdx', 'FN44', 'accounts_payable', '应付账款', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3'),
    ('tdx', 'FN52', 'current_portion_noncurrent_liabilities', '一年内到期的非流动负债', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3'),
    ('tdx', 'FN54', 'current_liabilities', '流动负债合计', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3'),
    ('tdx', 'FN55', 'long_term_borrowings', '长期借款', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3'),
    ('tdx', 'FN63', 'total_liabilities', '负债合计', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3'),
    ('tdx', 'FN69', 'noncontrolling_interests', '少数股东权益', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3'),
    ('tdx', 'FN72', 'total_equity', '所有者权益合计', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3'),
    ('tdx', 'FN114', 'capital_expenditure_cash', '购建固定资产、无形资产和其他长期资产支付的现金', 'CNY', 'monetary', DATE '2025-01-01', 'ytd', 'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3'),
    ('tdx', 'FN133', 'cash_and_cash_equivalents', '期末现金及现金等价物余额', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3'),
    ('tdx', 'FN136', 'depreciation_depletion', '固定资产折旧、油气资产折耗、生产性生物资产折旧', 'CNY', 'monetary', DATE '2025-01-01', 'ytd', 'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3'),
    ('tdx', 'FN137', 'intangible_amortization', '无形资产摊销', 'CNY', 'monetary', DATE '2025-01-01', 'ytd', 'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3'),
    ('tdx', 'FN138', 'deferred_expense_amortization', '长期待摊费用摊销', 'CNY', 'monetary', DATE '2025-01-01', 'ytd', 'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3'),
    ('tdx', 'FN271', 'equity_parent', '归属于母公司股东权益', 'CNY', 'monetary', DATE '2025-01-01', 'instant', 'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3');
