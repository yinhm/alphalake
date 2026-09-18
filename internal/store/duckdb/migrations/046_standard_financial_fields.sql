-- 源目录负责映射；通用字段目录负责财务语义。来源编号不参与标准派生。
UPDATE fundamental.provider_field SET period_basis='quarter'
WHERE source='tdx' AND provider_field IN ('FN230','FN231','FN232','FN233','FN234','FN235','FN236','FN237');
UPDATE fundamental.provider_field SET period_basis='instant'
WHERE source='tdx' AND provider_field='FN238';

CREATE TABLE fundamental.field (
    canonical_field VARCHAR PRIMARY KEY,
    unit VARCHAR NOT NULL,
    value_kind VARCHAR NOT NULL,
    period_basis VARCHAR NOT NULL CHECK (period_basis IN ('instant','quarter','ytd'))
);
INSERT INTO fundamental.field
SELECT DISTINCT canonical_field, unit, value_kind, period_basis
FROM fundamental.provider_field WHERE canonical_field IS NOT NULL;

-- 只修正股本的期间标签；原数值、源位、事实身份与源血缘保持不变。
UPDATE fundamental.fact SET period_type='instant'
WHERE canonical_field='total_shares' AND materializer_version<>'legacy';

-- 证券范围进入共享事实CTE；缺省保持全市场，反向区间不返回证券。
-- 按公告 ASOF 先选各期版本，再组合；缺期返回 NULL 和缺口，不拿部分和冒充 TTM。
CREATE OR REPLACE MACRO fundamental.ttm_asof(as_of_time, end_period, min_instrument_id := NULL, max_instrument_id := NULL) AS TABLE
WITH facts AS (
    SELECT * FROM fundamental.fact_asof(CAST(as_of_time AS TIMESTAMPTZ))
    WHERE (min_instrument_id IS NULL OR instrument_id >= min_instrument_id)
      AND (max_instrument_id IS NULL OR instrument_id <= max_instrument_id)
      AND report_period <= CAST(end_period AS DATE)
      AND materializer_version <> 'legacy'
), instruments AS (
    SELECT DISTINCT instrument_id, primary_source, provider_code, statement_scope FROM facts
), series AS (
    SELECT i.*, m.canonical_field, m.unit,
        CASE WHEN m.value_kind='monetary' THEN 'CNY' END AS currency,
        m.period_basis AS basis
    FROM instruments i CROSS JOIN fundamental.field m
    WHERE CAST(end_period AS DATE)=last_day(CAST(end_period AS DATE))
      AND month(CAST(end_period AS DATE)) IN (3,6,9,12)
), requirements AS (
    SELECT s.*, 0 AS ordinal, CAST(end_period AS DATE) AS required_period, 1 AS coefficient
    FROM series s WHERE basis IS NOT NULL
    UNION ALL
    SELECT s.*, CAST(n AS INTEGER), last_day(CAST(end_period AS DATE)-n*INTERVAL '3 months'), 1
    FROM series s CROSS JOIN range(1,4) r(n) WHERE basis='quarter'
    UNION ALL
    SELECT s.*, 1, make_date(year(CAST(end_period AS DATE))-1,12,31), 1
    FROM series s WHERE basis='ytd' AND month(CAST(end_period AS DATE))<>12
    UNION ALL
    SELECT s.*, 2, last_day(CAST(end_period AS DATE)-INTERVAL '1 year'), -1
    FROM series s WHERE basis='ytd' AND month(CAST(end_period AS DATE))<>12
), inputs AS (
    SELECT r.*, f.fact_id, f.source_filing_id, f.announcement_time, f.value, f.source_provider_field
    FROM requirements r LEFT JOIN facts f
      ON f.instrument_id=r.instrument_id AND f.primary_source=r.primary_source
     AND f.provider_code=r.provider_code AND f.statement_scope=r.statement_scope
     AND f.canonical_field=r.canonical_field
     AND f.unit=r.unit AND f.currency IS NOT DISTINCT FROM r.currency
     AND f.report_period=r.required_period
     AND f.period_type=CASE
         WHEN r.basis='instant' THEN 'instant'
         WHEN r.basis='quarter' THEN 'Q' || CAST(quarter(r.required_period) AS VARCHAR)
         WHEN month(r.required_period)=3 THEN 'Q1'
         WHEN month(r.required_period)=6 THEN 'H1'
         WHEN month(r.required_period)=9 AND r.basis='ytd' THEN '9M'
         WHEN month(r.required_period)=9 THEN 'Q3'
         ELSE 'FY' END
)
SELECT instrument_id, primary_source, provider_code, statement_scope, canonical_field,
    min(source_provider_field) AS source_provider_field, unit, currency,
    min(CAST(end_period AS DATE)) AS report_period,
    CASE WHEN basis='instant' THEN 'instant' ELSE 'TTM' END AS period_type,
    basis AS calculation_basis,
    CASE WHEN count(fact_id)=count(*) THEN sum(value*coefficient) END AS value,
    CASE WHEN count(fact_id)=count(*) THEN 'complete' ELSE 'missing_inputs' END AS coverage_status,
    count(*) AS required_inputs, count(fact_id) AS available_inputs,
    max(announcement_time) AS latest_input_announcement_time,
    list(required_period ORDER BY ordinal) AS input_periods,
    list(coefficient ORDER BY ordinal) AS input_coefficients,
    list(fact_id ORDER BY ordinal) AS source_fact_ids,
    list(source_filing_id ORDER BY ordinal) AS source_filing_ids,
    list(required_period ORDER BY ordinal) FILTER (WHERE fact_id IS NULL) AS missing_periods
FROM inputs
GROUP BY instrument_id, primary_source, provider_code, statement_scope, canonical_field,
    unit, currency, basis;

