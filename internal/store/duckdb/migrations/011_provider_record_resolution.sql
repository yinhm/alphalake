-- Durable identity-resolution evidence for raw provider financial records.
-- A package is complete when every record is either resolved to a canonical
-- instrument or explicitly acknowledged by an operator. Pending rows remain
-- replayable from the immutable artifact.
CREATE TABLE fundamental.provider_record_resolution (
    artifact_id BIGINT NOT NULL,
    source VARCHAR NOT NULL,
    source_file VARCHAR NOT NULL,
    report_period DATE NOT NULL,
    provider_code VARCHAR NOT NULL,
    market_marker USMALLINT NOT NULL,
    status VARCHAR NOT NULL,
    instrument_id BIGINT,
    identifier_value VARCHAR,
    reason VARCHAR,
    acknowledged_reason VARCHAR,
    acknowledged_at TIMESTAMPTZ,
    last_ingest_run_id BIGINT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(artifact_id, provider_code),
    CHECK(status IN ('resolved', 'pending', 'acknowledged'))
);
