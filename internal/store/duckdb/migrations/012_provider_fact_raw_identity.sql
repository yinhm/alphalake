-- Provider facts are immutable provider evidence whose raw identity is the
-- artifact revision + provider code + provider field. Canonical instrument_id
-- is a resolvable/enrichable link and therefore must not participate in the
-- provider-fact identity: historical identity corrections may legitimately
-- reassign the same raw fact to another canonical instrument.
CREATE SEQUENCE IF NOT EXISTS fundamental.provider_fact_id_seq START 1;

ALTER TABLE fundamental.provider_fact RENAME TO provider_fact_legacy;

CREATE TABLE fundamental.provider_fact (
    provider_fact_id BIGINT PRIMARY KEY DEFAULT nextval('fundamental.provider_fact_id_seq'),
    instrument_id BIGINT NOT NULL,
    source VARCHAR NOT NULL,
    report_period DATE NOT NULL,
    announcement_time TIMESTAMPTZ,
    provider_code VARCHAR,
    market_marker USMALLINT,
    provider_field VARCHAR NOT NULL,
    value DOUBLE,
    value_float32_bits UBIGINT,
    source_file VARCHAR,
    source_file_hash VARCHAR,
    artifact_id BIGINT,
    ingest_run_id BIGINT,
    revision_key VARCHAR NOT NULL DEFAULT '',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    UNIQUE(source, revision_key, provider_code, provider_field)
);

-- Legacy rows predate raw provider-code persistence. They remain queryable with
-- NULL provider_code and are reconciled/backfilled from a retained artifact the
-- next time that revision is replayed. NULLs intentionally do not collide under
-- the new raw-identity uniqueness rule.
INSERT INTO fundamental.provider_fact (
    instrument_id, source, report_period, announcement_time,
    provider_field, value, value_float32_bits, source_file, source_file_hash,
    artifact_id, ingest_run_id, revision_key, ingested_at
)
SELECT
    instrument_id, source, report_period, announcement_time,
    provider_field, value, value_float32_bits, source_file, source_file_hash,
    artifact_id, ingest_run_id, revision_key, ingested_at
FROM fundamental.provider_fact_legacy;

DROP TABLE fundamental.provider_fact_legacy;
