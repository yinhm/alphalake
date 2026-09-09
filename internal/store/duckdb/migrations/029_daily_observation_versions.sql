-- 保留升级后的已解析日线观测；不回填不存在的历史修订或原协议归档。
-- 现有 instrument 粒度的 A 股兼容层，尚不是 ADR 016 的多市场 listing 模型。
CREATE SEQUENCE market.daily_observation_id_seq START 1;
CREATE TABLE market.daily_observation (
 observation_id BIGINT PRIMARY KEY DEFAULT nextval('market.daily_observation_id_seq'),
 instrument_id BIGINT NOT NULL,
 trade_date DATE NOT NULL,
 open DECIMAL(20,6), high DECIMAL(20,6), low DECIMAL(20,6), close DECIMAL(20,6),
 volume BIGINT, amount DECIMAL(30,6), up_count BIGINT, down_count BIGINT,
 source VARCHAR NOT NULL,
 ingest_run_id BIGINT NOT NULL,
 recorded_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);
CREATE INDEX daily_observation_lookup ON market.daily_observation(instrument_id,trade_date,source);
