-- Harden the schema-only filing table before the first production CNINFO writer.
-- Unresolved filing observations must remain queryable, so instrument_id becomes
-- nullable until strict exchange/code resolution succeeds.
ALTER TABLE fundamental.filing ALTER COLUMN instrument_id DROP NOT NULL;

ALTER TABLE fundamental.filing ADD COLUMN IF NOT EXISTS provider_code VARCHAR NOT NULL DEFAULT '';
ALTER TABLE fundamental.filing ADD COLUMN IF NOT EXISTS exchange_mic VARCHAR;
ALTER TABLE fundamental.filing ADD COLUMN IF NOT EXISTS security_name VARCHAR;
ALTER TABLE fundamental.filing ADD COLUMN IF NOT EXISTS filing_variant VARCHAR NOT NULL DEFAULT 'other';
ALTER TABLE fundamental.filing ADD COLUMN IF NOT EXISTS raw_category VARCHAR;
ALTER TABLE fundamental.filing ADD COLUMN IF NOT EXISTS classifier_version VARCHAR NOT NULL DEFAULT 'legacy';
ALTER TABLE fundamental.filing ADD COLUMN IF NOT EXISTS is_correction BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE fundamental.filing ADD COLUMN IF NOT EXISTS corrects_filing_id BIGINT;
ALTER TABLE fundamental.filing ADD COLUMN IF NOT EXISTS resolution_status VARCHAR NOT NULL DEFAULT 'resolved';
ALTER TABLE fundamental.filing ADD COLUMN IF NOT EXISTS resolution_reason VARCHAR;
ALTER TABLE fundamental.filing ADD COLUMN IF NOT EXISTS catalogue_artifact_id BIGINT;
ALTER TABLE fundamental.filing ADD COLUMN IF NOT EXISTS provider_org_id VARCHAR;
ALTER TABLE fundamental.filing ADD COLUMN IF NOT EXISTS provider_column_id VARCHAR;
ALTER TABLE fundamental.filing ADD COLUMN IF NOT EXISTS provider_page_column VARCHAR;
ALTER TABLE fundamental.filing ADD COLUMN IF NOT EXISTS raw_announcement_time_ms BIGINT;
ALTER TABLE fundamental.filing ADD COLUMN IF NOT EXISTS ingest_run_id BIGINT;
ALTER TABLE fundamental.filing ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp;
ALTER TABLE fundamental.filing ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp;

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
