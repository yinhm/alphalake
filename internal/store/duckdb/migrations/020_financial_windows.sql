-- 按公告 ASOF 先选各期版本，再组合；缺期返回 NULL 和缺口，不拿部分和冒充 TTM。
CREATE MACRO fundamental.ttm_asof(as_of_time, end_period) AS TABLE
WITH facts AS (
    SELECT * FROM fundamental.fact_asof(CAST(as_of_time AS TIMESTAMPTZ))
    WHERE report_period <= CAST(end_period AS DATE)
      AND primary_source='tdx' AND materializer_version <> 'legacy'
), instruments AS (
    SELECT DISTINCT instrument_id, primary_source, provider_code, statement_scope FROM facts
), series AS (
    SELECT DISTINCT i.*, m.canonical_field, m.provider_field, m.unit,
        CASE WHEN m.value_kind='monetary' THEN 'CNY' END AS currency,
        CASE
            WHEN m.period_basis='instant' OR m.provider_field='FN238' THEN 'instant'
            WHEN m.period_basis='ytd' THEN 'ytd'
            WHEN m.provider_field IN ('FN230','FN231','FN232','FN233','FN234','FN235','FN236','FN237') THEN 'quarter'
        END AS basis
    FROM instruments i JOIN fundamental.provider_field m ON m.source=i.primary_source
    WHERE m.canonical_field IS NOT NULL
      AND (m.valid_from IS NULL OR m.valid_from <= CAST(end_period AS DATE))
      AND (m.valid_to IS NULL OR m.valid_to > CAST(end_period AS DATE))
      AND CAST(end_period AS DATE)=last_day(CAST(end_period AS DATE))
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
    SELECT r.*, f.fact_id, f.source_filing_id, f.announcement_time, f.value
    FROM requirements r LEFT JOIN facts f
      ON f.instrument_id=r.instrument_id AND f.primary_source=r.primary_source
     AND f.provider_code=r.provider_code AND f.statement_scope=r.statement_scope
     AND f.canonical_field=r.canonical_field AND f.source_provider_field=r.provider_field
     AND f.unit=r.unit AND f.currency IS NOT DISTINCT FROM r.currency
     AND f.report_period=r.required_period
     AND f.period_type=CASE
         WHEN r.basis='instant' AND r.provider_field<>'FN238' THEN 'instant'
         WHEN r.basis='quarter' THEN 'Q' || CAST(quarter(r.required_period) AS VARCHAR)
         WHEN month(r.required_period)=3 THEN 'Q1'
         WHEN month(r.required_period)=6 THEN 'H1'
         WHEN month(r.required_period)=9 AND r.basis='ytd' THEN '9M'
         WHEN month(r.required_period)=9 THEN 'Q3'
         ELSE 'FY' END
)
SELECT instrument_id, primary_source, provider_code, statement_scope, canonical_field,
    provider_field AS source_provider_field, unit, currency,
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
    provider_field, unit, currency, basis;

-- 十二月期末 TTM 即该自然年度；存量仍标 instant，不转换为年度流量。
CREATE MACRO fundamental.annual_asof(as_of_time, report_year) AS TABLE
SELECT * REPLACE (CASE WHEN period_type='instant' THEN 'instant' ELSE 'FY' END AS period_type)
FROM fundamental.ttm_asof(as_of_time, make_date(CAST(report_year AS INTEGER),12,31));
