-- Canonical units for the first reviewed TDX mapping set. Monetary fields are
-- normalized to CNY yuan; total_shares is normalized to shares. The provider raw
-- float32 bits remain available in fundamental.provider_fact.
UPDATE fundamental.provider_field
SET unit='CNY', value_kind='monetary'
WHERE source='tdx' AND provider_field IN (
    'FN230','FN231','FN232','FN233','FN234','FN235','FN236','FN237'
);

UPDATE fundamental.provider_field
SET unit='share', value_kind='shares'
WHERE source='tdx' AND provider_field='FN238';

CREATE SEQUENCE IF NOT EXISTS fundamental.fact_id_seq START 1;
ALTER TABLE fundamental.fact RENAME TO fact_legacy;

-- Canonical fact identity follows immutable provider record identity. The
-- canonical instrument, filing link and canonical field may be corrected later
-- without leaving duplicate facts for the same raw provider field revision.
CREATE TABLE fundamental.fact (
    fact_id BIGINT PRIMARY KEY DEFAULT nextval('fundamental.fact_id_seq'),
    instrument_id BIGINT NOT NULL,
    canonical_field VARCHAR NOT NULL,
    report_period DATE NOT NULL,
    announcement_time TIMESTAMPTZ NOT NULL,
    period_type VARCHAR NOT NULL,
    statement_scope VARCHAR NOT NULL,
    currency VARCHAR,
    unit VARCHAR NOT NULL,
    value DECIMAL(38,10) NOT NULL,
    primary_source VARCHAR NOT NULL,
    source_provider_field VARCHAR NOT NULL,
    provider_code VARCHAR,
    provider_fact_id BIGINT,
    source_filing_id BIGINT NOT NULL,
    revision_key VARCHAR NOT NULL,
    normalization_rule VARCHAR NOT NULL,
    materializer_version VARCHAR NOT NULL,
    ingest_run_id BIGINT,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    UNIQUE(primary_source, revision_key, provider_code, source_provider_field)
);

-- The previous table had no production writer. Preserve any manually-created
-- rows without claiming raw provider identity that did not exist in that schema.
INSERT INTO fundamental.fact (
    instrument_id, canonical_field, report_period, announcement_time,
    period_type, statement_scope, currency, unit, value,
    primary_source, source_provider_field, source_filing_id, revision_key,
    normalization_rule, materializer_version, ingested_at
)
SELECT
    instrument_id, canonical_field, report_period, announcement_time,
    COALESCE(period_type, 'unknown'), COALESCE(statement_scope, 'unknown'),
    currency, COALESCE(unit, 'unknown'), value,
    primary_source, COALESCE(source_provider_field, canonical_field),
    COALESCE(source_filing_id, 0), revision_key,
    'legacy', 'legacy', ingested_at
FROM fundamental.fact_legacy;

DROP TABLE fundamental.fact_legacy;

CREATE OR REPLACE VIEW fundamental.fact_latest AS
SELECT * EXCLUDE (fact_rank)
FROM (
    SELECT
        f.*,
        row_number() OVER (
            PARTITION BY instrument_id, canonical_field, report_period
            ORDER BY announcement_time DESC, fact_id DESC
        ) AS fact_rank
    FROM fundamental.fact f
)
WHERE fact_rank=1;

CREATE OR REPLACE MACRO fundamental.fact_asof(as_of_time) AS TABLE
SELECT * EXCLUDE (fact_rank)
FROM (
    SELECT
        f.*,
        row_number() OVER (
            PARTITION BY instrument_id, canonical_field, report_period
            ORDER BY announcement_time DESC, fact_id DESC
        ) AS fact_rank
    FROM fundamental.fact f
    WHERE announcement_time <= as_of_time
)
WHERE fact_rank=1;
