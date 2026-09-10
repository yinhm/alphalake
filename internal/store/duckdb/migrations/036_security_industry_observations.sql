-- 来源证券分类观察，不宣称已解析 instrument_id 或未知的来源生效日期。
CREATE SEQUENCE reference.security_industry_id_seq START 1;
CREATE TABLE reference.security_industry (
    observation_id BIGINT PRIMARY KEY DEFAULT nextval('reference.security_industry_id_seq'),
    release_id BIGINT NOT NULL,
    artifact_id BIGINT NOT NULL,
    source_locator VARCHAR NOT NULL CHECK (length(trim(source_locator)) > 0),
    exchange_ticker VARCHAR NOT NULL CHECK (length(trim(exchange_ticker)) > 0),
    industry_node_id BIGINT NOT NULL,
    raw_payload VARCHAR NOT NULL CHECK (json_valid(raw_payload)),
    UNIQUE(release_id, exchange_ticker),
    UNIQUE(release_id, source_locator)
);
