-- 保留原观测 ID、发布版本和共享序列，不改变既有固定版本出口契约。
ALTER TABLE reference.country_risk RENAME TO country_risk_before_split;
CREATE TABLE reference.country_risk (
    observation_id BIGINT PRIMARY KEY DEFAULT nextval('reference.country_risk_id_seq'),
    release_id BIGINT NOT NULL,
    artifact_id BIGINT NOT NULL,
    source_locator VARCHAR NOT NULL CHECK (length(trim(source_locator)) > 0),
    raw_value VARCHAR NOT NULL,
    raw_unit VARCHAR NOT NULL CHECK (length(trim(raw_unit)) > 0),
    subject_kind VARCHAR NOT NULL CHECK (subject_kind IN ('country')),
    subject_code VARCHAR NOT NULL CHECK (length(trim(subject_code)) > 0),
    observation_date DATE NOT NULL,
    metric_code VARCHAR NOT NULL CHECK (metric_code IN ('country_risk_premium', 'sovereign_default_spread')),
    method_code VARCHAR NOT NULL CHECK (length(trim(method_code)) > 0),
    value DECIMAL(38,12),
    value_status VARCHAR NOT NULL CHECK (value_status IN ('reported', 'missing', 'not_applicable')),
    CHECK ((value_status = 'reported' AND value IS NOT NULL) OR (value_status <> 'reported' AND value IS NULL)),
    UNIQUE(release_id, subject_kind, subject_code, observation_date, metric_code, method_code)
);

CREATE TABLE reference.equity_risk_premium (
    observation_id BIGINT PRIMARY KEY DEFAULT nextval('reference.country_risk_id_seq'),
    release_id BIGINT NOT NULL,
    artifact_id BIGINT NOT NULL,
    source_locator VARCHAR NOT NULL CHECK (length(trim(source_locator)) > 0),
    raw_value VARCHAR NOT NULL,
    raw_unit VARCHAR NOT NULL CHECK (length(trim(raw_unit)) > 0),
    subject_kind VARCHAR NOT NULL CHECK (subject_kind IN ('country', 'market_group')),
    subject_code VARCHAR NOT NULL CHECK (length(trim(subject_code)) > 0),
    observation_date DATE NOT NULL,
    metric_code VARCHAR NOT NULL CHECK (metric_code IN ('mature_market_erp', 'total_equity_risk_premium')),
    method_code VARCHAR NOT NULL CHECK (length(trim(method_code)) > 0),
    value DECIMAL(38,12),
    value_status VARCHAR NOT NULL CHECK (value_status IN ('reported', 'missing', 'not_applicable')),
    CHECK ((value_status = 'reported' AND value IS NOT NULL) OR (value_status <> 'reported' AND value IS NULL)),
    CHECK ((metric_code = 'mature_market_erp' AND subject_kind = 'market_group') OR
           (metric_code <> 'mature_market_erp' AND subject_kind = 'country')),
    UNIQUE(release_id, subject_kind, subject_code, observation_date, metric_code, method_code)
);


INSERT INTO reference.country_risk SELECT * FROM reference.country_risk_before_split
 WHERE metric_code IN ('country_risk_premium','sovereign_default_spread');
INSERT INTO reference.equity_risk_premium SELECT * FROM reference.country_risk_before_split
 WHERE metric_code IN ('mature_market_erp','total_equity_risk_premium');
DROP TABLE reference.country_risk_before_split;
CREATE VIEW reference.risk_observation AS
 SELECT * FROM reference.country_risk UNION ALL SELECT * FROM reference.equity_risk_premium;
