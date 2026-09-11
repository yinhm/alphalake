-- 2024 H1/Q3/FY 安克合并报表逐位核验见 anker-cash-history-2024。
-- 仅扩展 FN114 的累计元口径到最早已审核报告期，不放宽其他字段。
UPDATE fundamental.provider_field
SET valid_from=DATE '2024-06-30',
    notes='TDX official FN catalogue; cumulative raw yuan verified against consolidated CNINFO statements: Anker 2024 H1/Q3/FY and existing 2025 H1/Q3 samples'
WHERE source='tdx' AND provider_field='FN114';
