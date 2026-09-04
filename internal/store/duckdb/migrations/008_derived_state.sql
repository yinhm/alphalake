CREATE TABLE meta.derived_state (
    dataset VARCHAR NOT NULL,
    instrument_id BIGINT NOT NULL,
    source VARCHAR NOT NULL,
    method VARCHAR NOT NULL,
    input_signature VARCHAR NOT NULL,
    output_ingest_run_id BIGINT NOT NULL,
    calculated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY(dataset, instrument_id, source, method)
);
