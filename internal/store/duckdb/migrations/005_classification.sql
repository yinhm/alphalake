CREATE SCHEMA IF NOT EXISTS classification;

CREATE SEQUENCE IF NOT EXISTS classification.taxonomy_id_seq START 1;
CREATE TABLE IF NOT EXISTS classification.taxonomy (
    taxonomy_id BIGINT PRIMARY KEY DEFAULT nextval('classification.taxonomy_id_seq'),
    source VARCHAR NOT NULL,
    taxonomy_code VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    taxonomy_type VARCHAR NOT NULL,
    UNIQUE(source, taxonomy_code)
);

CREATE SEQUENCE IF NOT EXISTS classification.node_id_seq START 1;
CREATE TABLE IF NOT EXISTS classification.node (
    node_id BIGINT PRIMARY KEY DEFAULT nextval('classification.node_id_seq'),
    taxonomy_id BIGINT NOT NULL,
    source_node_code VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    parent_node_id BIGINT,
    level INTEGER,
    source_symbol VARCHAR,
    UNIQUE(taxonomy_id, source_node_code)
);

CREATE TABLE IF NOT EXISTS classification.membership (
    instrument_id BIGINT NOT NULL,
    node_id BIGINT NOT NULL,
    effective_from DATE NOT NULL,
    effective_to DATE,
    source VARCHAR NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    artifact_id BIGINT,
    ingest_run_id BIGINT,
    PRIMARY KEY(instrument_id, node_id, effective_from, source)
);
