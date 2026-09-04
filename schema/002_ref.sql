CREATE SCHEMA IF NOT EXISTS ref;

CREATE TABLE IF NOT EXISTS ref.exchange (
    mic VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    country_code VARCHAR,
    timezone VARCHAR NOT NULL,
    currency VARCHAR
);

CREATE SEQUENCE IF NOT EXISTS ref.company_id_seq START 1;
CREATE TABLE IF NOT EXISTS ref.company (
    company_id BIGINT PRIMARY KEY DEFAULT nextval('ref.company_id_seq'),
    legal_name VARCHAR,
    short_name VARCHAR,
    country_code VARCHAR
);

CREATE SEQUENCE IF NOT EXISTS ref.instrument_id_seq START 1;
CREATE TABLE IF NOT EXISTS ref.instrument (
    instrument_id BIGINT PRIMARY KEY DEFAULT nextval('ref.instrument_id_seq'),
    instrument_type VARCHAR NOT NULL,
    exchange_mic VARCHAR,
    currency VARCHAR,
    company_id BIGINT,
    name VARCHAR,
    list_date DATE,
    delist_date DATE,
    status VARCHAR NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

CREATE SEQUENCE IF NOT EXISTS ref.instrument_identifier_id_seq START 1;
CREATE TABLE IF NOT EXISTS ref.instrument_identifier (
    instrument_identifier_id BIGINT PRIMARY KEY DEFAULT nextval('ref.instrument_identifier_id_seq'),
    instrument_id BIGINT NOT NULL,
    provider VARCHAR NOT NULL,
    identifier_type VARCHAR NOT NULL,
    identifier_value VARCHAR NOT NULL,
    valid_from DATE,
    valid_to DATE,
    is_primary BOOLEAN NOT NULL DEFAULT false,
    UNIQUE(provider, identifier_type, identifier_value, valid_from)
);

CREATE TABLE IF NOT EXISTS ref.trading_calendar (
    exchange_mic VARCHAR NOT NULL,
    trade_date DATE NOT NULL,
    is_open BOOLEAN NOT NULL,
    session_open TIME,
    session_close TIME,
    source VARCHAR NOT NULL,
    PRIMARY KEY(exchange_mic, trade_date, source)
);
