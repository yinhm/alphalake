-- Provider financial values and authoritative filing metadata retain separate
-- provenance. This table records the deterministic link between one immutable
-- provider-record revision and one disclosure-platform filing.
CREATE TABLE fundamental.provider_filing_link (
    provider_source VARCHAR NOT NULL,
    provider_revision_key VARCHAR NOT NULL,
    provider_artifact_id BIGINT NOT NULL,
    provider_code VARCHAR NOT NULL,
    report_period DATE NOT NULL,
    instrument_id BIGINT,
    filing_id BIGINT,
    status VARCHAR NOT NULL,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    link_method VARCHAR,
    reason VARCHAR,
    linker_version VARCHAR NOT NULL,
    linked_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY(provider_source, provider_revision_key, provider_code)
);
