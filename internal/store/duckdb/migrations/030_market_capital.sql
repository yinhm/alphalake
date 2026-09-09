-- 首批证据限定的公司、股份类别及上市身份；不回填旧主数据历史。
CREATE SEQUENCE ref.listing_id_seq START 1;
CREATE TABLE ref.listing (
 listing_id BIGINT PRIMARY KEY DEFAULT nextval('ref.listing_id_seq'),
 instrument_id BIGINT NOT NULL REFERENCES ref.instrument(instrument_id),
 exchange_mic VARCHAR NOT NULL CHECK(exchange_mic IN ('XSHG','XSHE','XHKG')),
 trading_currency VARCHAR NOT NULL CHECK(trading_currency IN ('CNY','HKD')),
 valid_from DATE NOT NULL, valid_to DATE,
 artifact_id BIGINT NOT NULL, recorded_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
 CHECK(valid_to IS NULL OR valid_to>valid_from),
 UNIQUE(instrument_id,exchange_mic,trading_currency,valid_from)
);
CREATE SEQUENCE ref.listing_identifier_id_seq START 1;
CREATE TABLE ref.listing_identifier (
 listing_identifier_id BIGINT PRIMARY KEY DEFAULT nextval('ref.listing_identifier_id_seq'),
 listing_id BIGINT NOT NULL REFERENCES ref.listing(listing_id),
 provider VARCHAR NOT NULL, identifier_type VARCHAR NOT NULL,
 identifier_value VARCHAR NOT NULL, market_namespace VARCHAR NOT NULL,
 valid_from DATE NOT NULL, valid_to DATE, artifact_id BIGINT NOT NULL,
 recorded_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
 CHECK(valid_to IS NULL OR valid_to>valid_from),
 UNIQUE(provider,identifier_type,market_namespace,identifier_value,valid_from)
);
CREATE SEQUENCE market.share_count_id_seq START 1;
CREATE TABLE market.share_count_observation (
 observation_id BIGINT PRIMARY KEY DEFAULT nextval('market.share_count_id_seq'),
 release_id BIGINT NOT NULL, artifact_id BIGINT NOT NULL,
 company_id BIGINT, instrument_id BIGINT,
 scope VARCHAR NOT NULL CHECK(scope IN ('company_total','share_class')),
 share_basis VARCHAR NOT NULL CHECK(share_basis IN ('issued','treasury','outstanding','free_float')),
 effective_date DATE NOT NULL,
 value DECIMAL(38,10) NOT NULL CHECK(value>=0), source_locator VARCHAR NOT NULL CHECK(length(trim(source_locator))>0),
 CHECK((scope='company_total' AND company_id IS NOT NULL AND instrument_id IS NULL) OR
       (scope='share_class' AND company_id IS NULL AND instrument_id IS NOT NULL)),
 UNIQUE(release_id,instrument_id,share_basis,effective_date),
 UNIQUE(release_id,company_id,share_basis,effective_date)
);
CREATE SEQUENCE market.listing_close_id_seq START 1;
CREATE TABLE market.listing_close_observation (
 observation_id BIGINT PRIMARY KEY DEFAULT nextval('market.listing_close_id_seq'),
 release_id BIGINT NOT NULL, artifact_id BIGINT NOT NULL, listing_id BIGINT NOT NULL,
 trade_date DATE NOT NULL, close DECIMAL(20,6) NOT NULL CHECK(close>0),
 raw_value VARCHAR NOT NULL, source_locator VARCHAR NOT NULL,
 adjustment VARCHAR NOT NULL CHECK(adjustment='unadjusted'),
 UNIQUE(release_id,listing_id,trade_date)
);
