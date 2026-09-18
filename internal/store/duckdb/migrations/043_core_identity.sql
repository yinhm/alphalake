-- core schema 和序列由版本043迁移前置逻辑在同一事务中建立。

CREATE TABLE core.exchange (
    mic VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    country_code VARCHAR,
    timezone VARCHAR NOT NULL,
    currency VARCHAR
);

CREATE TABLE core.company (
    company_id BIGINT PRIMARY KEY DEFAULT nextval('core.company_id_seq'),
    legal_name VARCHAR,
    short_name VARCHAR,
    country_code VARCHAR
);

CREATE TABLE core.instrument (
    instrument_id BIGINT PRIMARY KEY DEFAULT nextval('core.instrument_id_seq'),
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

CREATE TABLE core.instrument_identifier (
    instrument_identifier_id BIGINT PRIMARY KEY DEFAULT nextval('core.instrument_identifier_id_seq'),
    instrument_id BIGINT NOT NULL,
    provider VARCHAR NOT NULL,
    identifier_type VARCHAR NOT NULL,
    identifier_value VARCHAR NOT NULL,
    valid_from DATE,
    valid_to DATE,
    is_primary BOOLEAN NOT NULL DEFAULT false,
    UNIQUE(provider, identifier_type, identifier_value, valid_from)
);

CREATE TABLE market.trading_calendar (
    exchange_mic VARCHAR NOT NULL,
    trade_date DATE NOT NULL,
    is_open BOOLEAN NOT NULL,
    session_open TIME,
    session_close TIME,
    source VARCHAR NOT NULL,
    PRIMARY KEY(exchange_mic, trade_date, source)
);
-- 首批证据限定的公司、股份类别及上市身份；不回填旧主数据历史。
CREATE TABLE core.listing (
 listing_id BIGINT PRIMARY KEY DEFAULT nextval('core.listing_id_seq'),
 instrument_id BIGINT NOT NULL REFERENCES core.instrument(instrument_id),
 exchange_mic VARCHAR NOT NULL CHECK(exchange_mic IN ('XSHG','XSHE','XHKG')),
 trading_currency VARCHAR NOT NULL CHECK(trading_currency IN ('CNY','HKD')),
 valid_from DATE NOT NULL, valid_to DATE,
 artifact_id BIGINT NOT NULL, recorded_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
 CHECK(valid_to IS NULL OR valid_to>valid_from),
 UNIQUE(instrument_id,exchange_mic,trading_currency,valid_from)
);
CREATE TABLE core.listing_identifier (
 listing_identifier_id BIGINT PRIMARY KEY DEFAULT nextval('core.listing_identifier_id_seq'),
 listing_id BIGINT NOT NULL REFERENCES core.listing(listing_id),
 provider VARCHAR NOT NULL, identifier_type VARCHAR NOT NULL,
 identifier_value VARCHAR NOT NULL, market_namespace VARCHAR NOT NULL,
 valid_from DATE NOT NULL, valid_to DATE, artifact_id BIGINT NOT NULL,
 recorded_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
 CHECK(valid_to IS NULL OR valid_to>valid_from),
 UNIQUE(provider,identifier_type,market_namespace,identifier_value,valid_from)
);
INSERT INTO core.exchange SELECT * FROM ref.exchange;
INSERT INTO core.company SELECT * FROM ref.company;
INSERT INTO core.instrument SELECT * FROM ref.instrument;
INSERT INTO core.instrument_identifier SELECT * FROM ref.instrument_identifier;
INSERT INTO core.listing SELECT * FROM ref.listing;
INSERT INTO core.listing_identifier SELECT * FROM ref.listing_identifier;
INSERT INTO market.trading_calendar SELECT * FROM ref.trading_calendar;
DROP TABLE ref.listing_identifier;
DROP TABLE ref.listing;
DROP TABLE ref.instrument_identifier;
DROP TABLE ref.instrument;
DROP TABLE ref.company;
DROP TABLE ref.exchange;
DROP TABLE ref.trading_calendar;
DROP SEQUENCE ref.listing_identifier_id_seq;
DROP SEQUENCE ref.listing_id_seq;
DROP SEQUENCE ref.instrument_identifier_id_seq;
DROP SEQUENCE ref.instrument_id_seq;
DROP SEQUENCE ref.company_id_seq;
DROP SCHEMA ref;
