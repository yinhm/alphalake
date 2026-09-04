CREATE SCHEMA IF NOT EXISTS fundamental;

CREATE SEQUENCE IF NOT EXISTS fundamental.filing_id_seq START 1;
CREATE TABLE IF NOT EXISTS fundamental.filing (
    filing_id BIGINT PRIMARY KEY DEFAULT nextval('fundamental.filing_id_seq'),
    instrument_id BIGINT NOT NULL,
    source VARCHAR NOT NULL,
    source_filing_id VARCHAR NOT NULL,
    filing_type VARCHAR,
    report_period DATE,
    announcement_time TIMESTAMPTZ,
    title VARCHAR,
    source_url VARCHAR,
    artifact_id BIGINT,
    sha256 VARCHAR,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    UNIQUE(source, source_filing_id)
);

CREATE TABLE IF NOT EXISTS fundamental.provider_field (
    source VARCHAR NOT NULL,
    provider_field VARCHAR NOT NULL,
    canonical_field VARCHAR,
    display_name VARCHAR,
    unit VARCHAR,
    value_kind VARCHAR,
    valid_from DATE,
    valid_to DATE,
    notes VARCHAR,
    PRIMARY KEY(source, provider_field, valid_from)
);

CREATE TABLE IF NOT EXISTS fundamental.provider_fact (
    instrument_id BIGINT NOT NULL,
    source VARCHAR NOT NULL,
    report_period DATE NOT NULL,
    announcement_time TIMESTAMPTZ,
    provider_field VARCHAR NOT NULL,
    value DOUBLE,
    source_file VARCHAR,
    source_file_hash VARCHAR,
    artifact_id BIGINT,
    ingest_run_id BIGINT,
    revision_key VARCHAR NOT NULL DEFAULT '',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY(instrument_id, source, report_period, provider_field, revision_key)
);

CREATE TABLE IF NOT EXISTS fundamental.fact (
    instrument_id BIGINT NOT NULL,
    canonical_field VARCHAR NOT NULL,
    report_period DATE NOT NULL,
    announcement_time TIMESTAMPTZ NOT NULL,
    period_type VARCHAR,
    statement_scope VARCHAR,
    currency VARCHAR,
    unit VARCHAR,
    value DECIMAL(38,10),
    primary_source VARCHAR NOT NULL,
    source_provider_field VARCHAR,
    source_filing_id BIGINT,
    revision_key VARCHAR NOT NULL DEFAULT '',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY(instrument_id, canonical_field, report_period, announcement_time, primary_source, revision_key)
);
