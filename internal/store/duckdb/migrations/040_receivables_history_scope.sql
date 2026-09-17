-- 安克2024年报合并其他应收款净额126612165.92元与真实gpcw20241231
-- FN13源位一致；不是关联方借款总额或坏账准备。仅扩展到已核验年末。
UPDATE fundamental.provider_field
SET valid_from=DATE '2024-12-31',
    notes='Net carrying amount, raw yuan, instant; Anker 2024 FY original p155 verified in company-inputs-20260917/anker-opening-2024; prior audited scope retained'
WHERE source='tdx' AND provider_field='FN13';
