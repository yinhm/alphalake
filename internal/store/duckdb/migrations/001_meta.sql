CREATE SCHEMA IF NOT EXISTS meta;

CREATE TABLE IF NOT EXISTS meta.schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    description VARCHAR NOT NULL
);

CREATE SEQUENCE IF NOT EXISTS meta.ingest_run_id_seq START 1;
CREATE TABLE IF NOT EXISTS meta.ingest_run (
    ingest_run_id BIGINT PRIMARY KEY DEFAULT nextval('meta.ingest_run_id_seq'),
    source VARCHAR NOT NULL,
    dataset VARCHAR NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    finished_at TIMESTAMPTZ,
    status VARCHAR NOT NULL,
    checkpoint_before VARCHAR,
    checkpoint_after VARCHAR,
    error_message VARCHAR
);

CREATE SEQUENCE IF NOT EXISTS meta.artifact_id_seq START 1;
CREATE TABLE IF NOT EXISTS meta.artifact (
    artifact_id BIGINT PRIMARY KEY DEFAULT nextval('meta.artifact_id_seq'),
    source VARCHAR NOT NULL,
    dataset VARCHAR NOT NULL,
    source_locator VARCHAR NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    sha256 VARCHAR NOT NULL,
    content_length BIGINT NOT NULL,
    media_type VARCHAR,
    local_path VARCHAR,
    parser_version VARCHAR,
    ingest_run_id BIGINT,
    UNIQUE(source, dataset, source_locator, sha256)
);

CREATE TABLE IF NOT EXISTS meta.checkpoint (
    source VARCHAR NOT NULL,
    dataset VARCHAR NOT NULL,
    checkpoint_key VARCHAR NOT NULL,
    checkpoint_value VARCHAR NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY(source, dataset, checkpoint_key)
);

CREATE SEQUENCE IF NOT EXISTS meta.validation_result_id_seq START 1;
CREATE TABLE IF NOT EXISTS meta.validation_result (
    validation_result_id BIGINT PRIMARY KEY DEFAULT nextval('meta.validation_result_id_seq'),
    ingest_run_id BIGINT,
    source VARCHAR NOT NULL,
    dataset VARCHAR NOT NULL,
    rule_code VARCHAR NOT NULL,
    severity VARCHAR NOT NULL,
    subject_type VARCHAR,
    subject_key VARCHAR,
    observed_value VARCHAR,
    expected_value VARCHAR,
    passed BOOLEAN NOT NULL,
    details VARCHAR,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

INSERT INTO meta.schema_version(version, description)
SELECT 1, 'initial metadata schema'
WHERE NOT EXISTS (SELECT 1 FROM meta.schema_version WHERE version = 1);
