CREATE SCHEMA IF NOT EXISTS market;

CREATE TABLE IF NOT EXISTS market.ohlcv_daily (
    instrument_id BIGINT NOT NULL,
    trade_date DATE NOT NULL,
    open DECIMAL(20,6),
    high DECIMAL(20,6),
    low DECIMAL(20,6),
    close DECIMAL(20,6),
    volume BIGINT,
    amount DECIMAL(30,6),
    up_count BIGINT,
    down_count BIGINT,
    source VARCHAR NOT NULL,
    source_record_id VARCHAR,
    artifact_id BIGINT,
    ingest_run_id BIGINT,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY(instrument_id, trade_date, source)
);

CREATE SEQUENCE IF NOT EXISTS market.corporate_action_id_seq START 1;
CREATE TABLE IF NOT EXISTS market.corporate_action (
    corporate_action_id BIGINT PRIMARY KEY DEFAULT nextval('market.corporate_action_id_seq'),
    instrument_id BIGINT NOT NULL,
    action_date DATE NOT NULL,
    action_type VARCHAR NOT NULL,
    source_category INTEGER,
    cash_dividend_per_10 DECIMAL(30,10),
    rights_price DECIMAL(30,10),
    bonus_or_split_per_10 DECIMAL(30,10),
    rights_per_10 DECIMAL(30,10),
    scale_factor DECIMAL(30,12),
    raw_c1 DOUBLE,
    raw_c2 DOUBLE,
    raw_c3 DOUBLE,
    raw_c4 DOUBLE,
    source VARCHAR NOT NULL,
    source_record_id VARCHAR,
    artifact_id BIGINT,
    ingest_run_id BIGINT,
    UNIQUE(instrument_id, action_date, source, source_category, source_record_id)
);

CREATE TABLE IF NOT EXISTS market.share_capital (
    instrument_id BIGINT NOT NULL,
    effective_date DATE NOT NULL,
    float_shares BIGINT,
    total_shares BIGINT,
    source_category INTEGER,
    source VARCHAR NOT NULL,
    source_record_id VARCHAR,
    artifact_id BIGINT,
    ingest_run_id BIGINT,
    PRIMARY KEY(instrument_id, effective_date, source, source_category)
);

CREATE TABLE IF NOT EXISTS market.adjustment_segment (
    instrument_id BIGINT NOT NULL,
    effective_from DATE NOT NULL,
    effective_to DATE,
    qfq_mul DOUBLE NOT NULL,
    qfq_add DOUBLE NOT NULL,
    hfq_mul DOUBLE NOT NULL,
    hfq_add DOUBLE NOT NULL,
    method VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    calculated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY(instrument_id, effective_from, method, source)
);
