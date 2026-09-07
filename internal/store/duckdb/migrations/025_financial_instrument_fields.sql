-- 原文及万元源位验证见 financial-instruments-2026；财务业务与附注分类分开。
INSERT INTO fundamental.provider_field
    (source, provider_field, canonical_field, display_name, unit, value_kind, valid_from, period_basis, value_multiplier, notes)
VALUES
    ('tdx','FN9','trading_financial_assets','交易性金融资产','CNY','monetary',DATE '2025-01-01','instant',1,'Total trading financial assets; debt/equity/derivative split remains a note supplement; financial-instruments-2026'),
    ('tdx','FN59','provisions','预计负债','CNY','monetary',DATE '2025-01-01','instant',1,'Total provisions; sample lease/nonoperating classification is not universal; financial-instruments-2026'),
    ('tdx','FN299','other_equity_instruments','其他权益工具','CNY','monetary',DATE '2025-01-01','instant',1,'All other equity instruments; sample convertible equity component is not market value; financial-instruments-2026'),
    ('tdx','FN403','funds_lent','拆出资金','CNY','monetary',DATE '2025-01-01','instant',10000,'Consolidated interbank business assets; not unrestricted operating cash; financial-instruments-2026'),
    ('tdx','FN409','financial_assets_purchased_under_resale_agreements','买入返售金融资产','CNY','monetary',DATE '2025-01-01','instant',10000,'Financial assets under resale agreements; not operating cash; financial-instruments-2026'),
    ('tdx','FN411','loans_and_advances_noncurrent','发放贷款及垫款（非流动）','CNY','monetary',DATE '2025-01-01','instant',10000,'Noncurrent loans and advances; not related-party receivable note components; financial-instruments-2026'),
    ('tdx','FN413','deposits_and_interbank_placements','吸收存款及同业存放','CNY','monetary',DATE '2025-01-01','instant',10000,'External deposits at consolidated financial subsidiary; not issuer interest-bearing borrowing; financial-instruments-2026'),
    ('tdx','FN430','debt_investments','债权投资','CNY','monetary',DATE '2025-01-01','instant',10000,'Carrying amount, not market value or debt asset maturity split; financial-instruments-2026'),
    ('tdx','FN431','other_debt_investments','其他债权投资','CNY','monetary',DATE '2025-01-01','instant',10000,'Carrying amount, not market value; financial-instruments-2026'),
    ('tdx','FN433','other_noncurrent_financial_assets','其他非流动金融资产','CNY','monetary',DATE '2025-01-01','instant',10000,'Total other noncurrent financial assets; not equity-only funds universally; financial-instruments-2026'),
    ('tdx','FN434','contract_liabilities','合同负债','CNY','monetary',DATE '2025-01-01','instant',10000,'Contract liabilities; distinct from taxes or advances received; financial-instruments-2026'),
    ('tdx','FN437','receivables_financing','应收款项融资','CNY','monetary',DATE '2025-01-01','instant',10000,'Receivables financing; not notes receivable FN10 or receivables FN11; financial-instruments-2026'),
    ('tdx','FN506','financial_business_interest_income','金融业务利息收入','CNY','monetary',DATE '2025-01-01','ytd',10000,'Income statement financial-business interest income; not treasury interest FN306; financial-instruments-2026'),
    ('tdx','FN509','financial_business_interest_expense','金融业务利息支出','CNY','monetary',DATE '2025-01-01','ytd',10000,'Income statement financial-business interest expense; not finance-cost interest FN305; financial-instruments-2026'),
    ('tdx','FN510','financial_business_fee_expense','手续费及佣金支出','CNY','monetary',DATE '2025-01-01','ytd',10000,'Income statement financial-business fee expense; financial-instruments-2026'),
    ('tdx','FN520','credit_impairment_income','信用减值损益（收益正、损失负）','CNY','monetary',DATE '2025-01-01','ytd',10000,'2019 income statement format; gain positive/loss negative, not CF allowance FN580; financial-instruments-2026'),
    ('tdx','FN579','investment_property_depreciation_amortization','投资性房地产折旧及摊销','CNY','monetary',DATE '2025-01-01','ytd',10000,'Cashflow reconciliation investment property depreciation; not FN136 or FN581; financial-instruments-2026');
