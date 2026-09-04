-- Legacy compatibility assumption:
-- rows in market.share_capital were produced by AlphaLake's supported writer,
-- which always populated source and source_category. Manually-created legacy
-- rows with NULL identity fields are outside the supported upgrade contract.
ALTER TABLE market.share_capital RENAME TO share_capital_legacy;

CREATE TABLE market.share_capital (
    instrument_id BIGINT NOT NULL,
    effective_date DATE NOT NULL,
    float_shares BIGINT,
    total_shares BIGINT,
    source_category INTEGER NOT NULL,
    source VARCHAR NOT NULL,
    source_record_id VARCHAR NOT NULL,
    artifact_id BIGINT,
    ingest_run_id BIGINT,
    PRIMARY KEY(instrument_id, effective_date, source, source_category, source_record_id)
);

INSERT INTO market.share_capital (
    instrument_id,
    effective_date,
    float_shares,
    total_shares,
    source_category,
    source,
    source_record_id,
    artifact_id,
    ingest_run_id
)
SELECT
    instrument_id,
    effective_date,
    float_shares,
    total_shares,
    source_category,
    source,
    COALESCE(
        source_record_id,
        'legacy:' || CAST(instrument_id AS VARCHAR) || ':' || CAST(effective_date AS VARCHAR) || ':' ||
        source || ':' || CAST(source_category AS VARCHAR)
    ),
    artifact_id,
    ingest_run_id
FROM market.share_capital_legacy;

DROP TABLE market.share_capital_legacy;
