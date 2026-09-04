-- Curated core mapping from TDX's official professional-financial FN catalog.
-- Keep this intentionally small: provider facts retain every FN field losslessly,
-- while canonical mappings expand only when their semantics are reviewed.
INSERT INTO fundamental.provider_field (
    source, provider_field, canonical_field, display_name, valid_from, notes
) VALUES
    ('tdx', 'FN230', 'revenue',                '营业收入',                         DATE '1900-01-01', 'TDX official professional-financial field'),
    ('tdx', 'FN231', 'operating_profit',       '营业利润',                         DATE '1900-01-01', 'TDX official professional-financial field'),
    ('tdx', 'FN232', 'net_income_parent',      '归属于母公司所有者的净利润',       DATE '1900-01-01', 'TDX official professional-financial field'),
    ('tdx', 'FN233', 'adjusted_net_income',    '扣除非经常性损益后的净利润',       DATE '1900-01-01', 'TDX official professional-financial field'),
    ('tdx', 'FN234', 'operating_cash_flow',    '经营活动产生的现金流量净额',       DATE '1900-01-01', 'TDX official professional-financial field'),
    ('tdx', 'FN235', 'investing_cash_flow',    '投资活动产生的现金流量净额',       DATE '1900-01-01', 'TDX official professional-financial field'),
    ('tdx', 'FN236', 'financing_cash_flow',    '筹资活动产生的现金流量净额',       DATE '1900-01-01', 'TDX official professional-financial field'),
    ('tdx', 'FN237', 'net_cash_increase',      '现金及现金等价物净增加额',         DATE '1900-01-01', 'TDX official professional-financial field'),
    ('tdx', 'FN238', 'total_shares',           '总股本',                           DATE '1900-01-01', 'TDX official professional-financial field')
ON CONFLICT(source, provider_field, valid_from) DO UPDATE SET
    canonical_field=excluded.canonical_field,
    display_name=excluded.display_name,
    notes=excluded.notes;
