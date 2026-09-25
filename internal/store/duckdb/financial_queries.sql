CREATE MACRO fundamental.ttm_asof (as_of_time, end_period, min_instrument_id := NULL, max_instrument_id := NULL, security_code := NULL) AS TABLE (WITH facts AS (SELECT * FROM fundamental.financial_observations_asof(security_code,NULL,end_period,as_of_time,min_instrument_id := min_instrument_id,max_instrument_id := max_instrument_id) WHERE (((min_instrument_id IS NULL) OR (instrument_id >= min_instrument_id)) AND ((max_instrument_id IS NULL) OR (instrument_id <= max_instrument_id)) AND (report_period <= CAST(end_period AS "DATE")) AND (materializer_version != 'legacy'))), instruments AS (SELECT DISTINCT instrument_id, primary_source, provider_code, statement_scope FROM facts), series AS (SELECT i.*, m.canonical_field, m.unit, CASE  WHEN ((m.value_kind = 'monetary')) THEN ('CNY') ELSE NULL END AS currency, m.period_basis AS basis FROM instruments AS i CROSS JOIN fundamental.field AS m WHERE m.value_kind IN ('monetary','shares') AND m.period_basis IN ('instant','quarter','ytd') AND ((CAST(end_period AS "DATE") = last_day(CAST(end_period AS "DATE"))) AND ("month"(CAST(end_period AS "DATE")) IN (3, 6, 9, 12)))), requirements AS ((SELECT s.*, 0 AS ordinal, CAST(end_period AS "DATE") AS required_period, 1 AS coefficient FROM series AS s WHERE (basis IS NOT NULL)) UNION ALL (SELECT s.*, CAST(n AS INTEGER), last_day((CAST(end_period AS "DATE") - (n * CAST('3 months' AS INTERVAL)))), 1 FROM series AS s CROSS JOIN "range"(1, 4) AS r(n) WHERE (basis = 'quarter'))UNION ALL (SELECT s.*, 1, make_date(("year"(CAST(end_period AS "DATE")) - 1), 12, 31), 1 FROM series AS s WHERE ((basis = 'ytd') AND ("month"(CAST(end_period AS "DATE")) != 12)))UNION ALL (SELECT s.*, 2, last_day((CAST(end_period AS "DATE") - CAST('1 year' AS INTERVAL))), -1 FROM series AS s WHERE ((basis = 'ytd') AND ("month"(CAST(end_period AS "DATE")) != 12)))), inputs AS (SELECT r.*, f.fact_id, f.source_filing_id, f.announcement_time, f."value", f.source_provider_field FROM requirements AS r LEFT JOIN facts AS f ON (((f.instrument_id = r.instrument_id) AND (f.primary_source = r.primary_source) AND (f.provider_code = r.provider_code) AND (f.statement_scope = r.statement_scope) AND (f.canonical_field = r.canonical_field) AND (f.unit = r.unit) AND (f.currency IS NOT DISTINCT FROM r.currency) AND (f.report_period = r.required_period) AND (f.period_type = CASE  WHEN ((r.basis = 'instant')) THEN ('instant') WHEN ((r.basis = 'quarter')) THEN (('Q' || CAST("quarter"(r.required_period) AS VARCHAR))) WHEN (("month"(r.required_period) = 3)) THEN ('Q1') WHEN (("month"(r.required_period) = 6)) THEN ('H1') WHEN ((("month"(r.required_period) = 9) AND (r.basis = 'ytd'))) THEN ('9M') WHEN (("month"(r.required_period) = 9)) THEN ('Q3') ELSE 'FY' END))))SELECT instrument_id, primary_source, provider_code, statement_scope, canonical_field, min(source_provider_field) AS source_provider_field, unit, currency, min(CAST(end_period AS "DATE")) AS report_period, CASE  WHEN ((basis = 'instant')) THEN ('instant') ELSE 'TTM' END AS period_type, basis AS calculation_basis, CASE  WHEN ((count(fact_id) = count_star())) THEN (sum(("value" * coefficient))) ELSE NULL END AS "value", CASE  WHEN ((count(fact_id) = count_star())) THEN ('complete') ELSE 'missing_inputs' END AS coverage_status, count_star() AS required_inputs, count(fact_id) AS available_inputs, max(announcement_time) AS latest_input_announcement_time, list(required_period ORDER BY ordinal) AS input_periods, list(coefficient ORDER BY ordinal) AS input_coefficients, list(fact_id ORDER BY ordinal) AS source_fact_ids, list(source_filing_id ORDER BY ordinal) AS source_filing_ids, list(required_period ORDER BY ordinal) FILTER (WHERE (fact_id IS NULL)) AS missing_periods FROM inputs GROUP BY instrument_id, primary_source, provider_code, statement_scope, canonical_field, unit, currency, basis);
CREATE MACRO fundamental.annual_asof (as_of_time, report_year) AS TABLE (SELECT * REPLACE (CASE  WHEN ((period_type = 'instant')) THEN ('instant') ELSE 'FY' END AS period_type) FROM fundamental.ttm_asof(as_of_time, make_date(CAST(report_year AS INTEGER), 12, 31)));

CREATE MACRO fundamental.statements_asof(as_of_time, report_end, security_code, include_evidence := false) AS TABLE (
 WITH identity_candidates AS (
  SELECT DISTINCT x.instrument_id,x.identifier_value
  FROM core.instrument_identifier x JOIN core.instrument i USING(instrument_id)
  WHERE x.provider='tdx' AND x.identifier_type='symbol' AND i.instrument_type<>'index'
    AND right(x.identifier_value,6)=security_code
    AND (x.valid_from IS NULL OR x.valid_from<=CAST(report_end AS DATE))
    AND (x.valid_to IS NULL OR x.valid_to>CAST(report_end AS DATE))
 ), identity AS (
  SELECT CASE WHEN count(*)=1 THEN min(instrument_id) END AS instrument_id,
   CASE WHEN count(*)=1 THEN 'resolved' WHEN count(*)=0 THEN 'unresolved_identity' ELSE 'ambiguous_identity' END AS identity_status
  FROM identity_candidates
 ), catalog AS (
  SELECT name AS field,min(display_name) AS label,statement,section,unit,value_kind,period_basis,
   mapping_status,review_reason,count(*) AS source_variants
  FROM fundamental.source_field WHERE source='tdx' AND statement<>'' AND name IS NOT NULL
  GROUP BY name,statement,section,unit,value_kind,period_basis,mapping_status,review_reason
 ), visible_source AS (
  SELECT s.name,r.source_record_id,r.artifact_id,l.filing_id,f.announcement_time,
   j.rule_code,
   row_number() OVER (PARTITION BY s.name ORDER BY f.announcement_time DESC,r.source_record_id DESC) AS rank
  FROM fundamental.source_record r
  JOIN meta.artifact a USING(artifact_id)
  JOIN fundamental.source_field s ON s.source=a.source AND s.source_index<=r.field_count
  JOIN fundamental.provider_filing_link l ON l.provider_artifact_id=r.artifact_id AND l.provider_code=r.provider_code AND l.status='linked'
  JOIN fundamental.filing f USING(filing_id)
  LEFT JOIN fundamental.statement_rejection j ON j.source_record_id=r.source_record_id AND list_contains(j.fields,s.name)
  CROSS JOIN identity i
  WHERE r.provider_code=security_code AND l.report_period=CAST(report_end AS DATE)
    AND l.instrument_id=i.instrument_id AND f.resolution_status='resolved'
    AND f.instrument_id=l.instrument_id AND f.report_period=l.report_period
    AND f.announcement_time<=CAST(as_of_time AS TIMESTAMPTZ)
 ), mappings AS (
  SELECT DISTINCT m.canonical_field,m.provider_field,m.valid_from,m.valid_to
  FROM fundamental.provider_field m
  JOIN fundamental.field f ON f.canonical_field=m.canonical_field AND f.unit=m.unit AND f.value_kind=m.value_kind AND f.period_basis=m.period_basis
  JOIN fundamental.source_field s ON s.source=m.source AND s.provider_field=m.provider_field AND s.name=m.canonical_field AND s.unit=m.unit AND s.value_multiplier=m.value_multiplier AND s.period_basis=m.period_basis
  WHERE m.source='tdx' AND m.value_multiplier IN (1,10000)
    AND m.valid_from<=CAST(report_end AS DATE) AND (m.valid_to IS NULL OR CAST(report_end AS DATE)<m.valid_to)
 ), facts AS (
  SELECT f.* FROM fundamental.financial_observations_asof(security_code,report_end,report_end,as_of_time) f CROSS JOIN identity i
  WHERE f.instrument_id=i.instrument_id AND f.report_period=CAST(report_end AS DATE) AND f.materializer_version<>'legacy'
 ), evaluated AS (
  SELECT i.instrument_id,i.identity_status,c.*,f.fact_id,f.value,f.period_type,f.currency,f.statement_scope,f.announcement_time,
   f.source_filing_id,f.source_record_id,f.source_provider_field,f.revision_key,
   v.source_record_id AS diagnostic_source_record_id,v.filing_id AS diagnostic_filing_id,
   CASE
    WHEN i.identity_status<>'resolved' THEN i.identity_status
    WHEN EXISTS(SELECT 1 FROM fundamental.provider_conflicts_asof(as_of_time) conflict WHERE conflict.source='tdx' AND conflict.provider_code=security_code AND conflict.report_period=CAST(report_end AS DATE)) THEN 'source_conflict'
    WHEN c.mapping_status NOT IN ('reviewed_mapping','official_mapping') THEN c.review_reason
    WHEN c.source_variants<>1 THEN 'ambiguous_source_variant'
    WHEN NOT EXISTS(SELECT 1 FROM mappings m WHERE m.canonical_field=c.field) THEN 'mapping_unavailable_for_period'
    WHEN f.fact_id IS NOT NULL AND f.unit=c.unit AND f.statement_scope='provider_default'
      AND f.currency IS NOT DISTINCT FROM CASE WHEN c.value_kind IN ('monetary','per_share') THEN 'CNY' END
      AND f.period_type=CASE WHEN c.period_basis IN ('instant','opening_instant') THEN c.period_basis
        WHEN c.period_basis='quarter' THEN 'Q'||CAST(quarter(CAST(report_end AS DATE)) AS VARCHAR)
        WHEN month(CAST(report_end AS DATE))=3 THEN 'Q1' WHEN month(CAST(report_end AS DATE))=6 THEN 'H1'
        WHEN month(CAST(report_end AS DATE))=9 THEN '9M' ELSE 'FY' END
      AND EXISTS(SELECT 1 FROM mappings m WHERE m.canonical_field=c.field AND m.provider_field=f.source_provider_field)
      THEN 'available'
    WHEN f.fact_id IS NOT NULL THEN 'standard_fact_semantics_mismatch'
    WHEN v.source_record_id IS NULL THEN 'no_linked_source_at_asof'
    WHEN v.rule_code='provider_value_not_finite' THEN 'invalid_source_value'
    WHEN v.rule_code='provider_zero_ambiguous' THEN 'source_zero_ambiguous'
    ELSE 'not_materialized'
   END AS status
  FROM catalog c CROSS JOIN identity i
  LEFT JOIN facts f ON f.canonical_field=c.field AND f.provider_code=security_code AND f.primary_source='tdx'
  LEFT JOIN visible_source v ON v.name=c.field AND v.rank=1
 )
 SELECT instrument_id,identity_status,security_code AS code,CAST(report_end AS DATE) AS report_period,
  CAST(as_of_time AS TIMESTAMPTZ) AS information_as_of,statement,section,field,label,unit,
  CASE WHEN value_kind IN ('monetary','per_share') THEN 'CNY' END AS currency,
  period_basis,CASE WHEN period_basis='opening_instant' THEN make_date(year(CAST(report_end AS DATE))-1,12,31)
    WHEN period_basis='instant' THEN CAST(report_end AS DATE) END AS balance_date,
  mapping_status AS mapping_review,review_reason,status,
  CASE WHEN status='available' THEN value END AS value,
  CASE WHEN status='available' THEN announcement_time END AS announcement_time,
  CASE WHEN include_evidence THEN to_json(struct_pack(
    fact_id:=CASE WHEN status='available' THEN fact_id END,
    source_record_id:=coalesce(source_record_id,diagnostic_source_record_id),
    filing_id:=coalesce(source_filing_id,diagnostic_filing_id),
    provider_field:=source_provider_field,source_revision:=revision_key)) END AS source_evidence
 FROM evaluated
 WHERE CAST(report_end AS DATE)=last_day(CAST(report_end AS DATE)) AND month(CAST(report_end AS DATE)) IN (3,6,9,12)
);
