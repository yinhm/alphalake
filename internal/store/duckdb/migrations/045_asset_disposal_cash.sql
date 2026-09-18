-- TDX 官方 FN110 定义 + 安克三期原件/源位；区别于FN301处置损益。
-- 源零值仍按既有歧义规则拒绝，未披露不补零；不自动生成完整净再投资。
INSERT INTO fundamental.provider_field
(source,provider_field,canonical_field,display_name,unit,value_kind,valid_from,period_basis,value_multiplier,notes)
VALUES ('tdx','FN110','long_lived_asset_disposal_cash','处置固定资产、无形资产和其他长期资产收回的现金净额','CNY','monetary',DATE '2025-01-01','ytd',1,
'TDX official FN110; Anker 2025H1/FY and 2026H1 consolidated cashflow source bits checked; cash proceeds, not disposal gain, subsidiary disposal or financial investment recovery; zero ambiguity retained');
