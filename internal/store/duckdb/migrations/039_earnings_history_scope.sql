-- 安克2024 H1/9M/FY：18个原值及48个原文金额见
-- anker-cash-history-2024/earnings-verified.json。只扩展已核验的累计元口径。
UPDATE fundamental.provider_field
SET valid_from=DATE '2024-06-30',
    notes=provider_field || ': cumulative raw yuan; original consolidated Anker 2024 H1/9M/FY verified in anker-cash-history-2024/earnings-verified.json; prior audited scope retained; interest-note composition confirmed for H1/FY only'
WHERE source='tdx' AND provider_field IN ('FN86','FN305','FN306','FN83','FN82','FN301');
