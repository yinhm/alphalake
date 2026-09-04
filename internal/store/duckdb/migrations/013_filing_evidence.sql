-- Harden the schema-only filing table before the first production CNINFO writer.
-- DuckDB cannot add constrained columns in-place, so rebuild the table once.
-- Unresolved filing observations remain queryable with nullable instrument_id.
ALTER TABLE fundamental.filing RENAME TO filing_legacy;

CREATE TABLE fundamental.filing (
    filing_id BIGINT PRIMARY KEY DEFAULT nextval('fundamental.filing_id_seq'),
    instrument_id BIGINT,
    source VARCHAR NOT NULL,
    source_filing_id VARCHAR NOT NULL,
    provider_code VARCHAR NOT NULL DEFAULT '',
    exchange_mic VARCHAR,
    security_name VARCHAR,
    filing_type VARCHAR,
    filing_variant VARCHAR NOT NULL DEFAULT 'other',
    report_period DATE,
    announcement_time TIMESTAMPTZ,
    title VARCHAR,
    source_url VARCHAR,
    raw_category VARCHAR,
    classifier_version VARCHAR NOT NULL DEFAULT 'legacy',
    is_correction BOOLEAN NOT NULL DEFAULT false,
    corrects_filing_id BIGINT,
    resolution_status VARCHAR NOT NULL DEFAULT 'resolved',
    resolution_reason VARCHAR,
    catalogue_artifact_id BIGINT,
    artifact_id BIGINT,
    sha256 VARCHAR,
    provider_org_id VARCHAR,
    provider_column_id VARCHAR,
    provider_page_column VARCHAR,
    raw_announcement_time_ms BIGINT,
    ingest_run_id BIGINT,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    UNIQUE(source, source_filing_id),
    CHECK(resolution_status IN ('resolved', 'pending', 'acknowledged'))
);

INSERT INTO fundamental.filing (
    filing_id, instrument_id, source, source_filing_id,
    filing_type, report_period, announcement_time, title, source_url,
    artifact_id, sha256, first_seen_at, last_seen_at, ingested_at
)
SELECT
    filing_id, instrument_id, source, source_filing_id,
    filing_type, report_period, announcement_time, title, source_url,
    artifact_id, sha256, ingested_at, ingested_at, ingested_at
FROM fundamental.filing_legacy;

DROP TABLE fundamental.filing_legacy;

-- A filing row points at the currently selected document artifact. Every observed
-- immutable document revision remains queryable through this history table.
CREATE TABLE fundamental.filing_document (
    filing_id BIGINT NOT NULL,
    artifact_id BIGINT NOT NULL,
    source_url VARCHAR NOT NULL,
    sha256 VARCHAR NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    ingest_run_id BIGINT,
    PRIMARY KEY(filing_id, artifact_id)
);
