-- 为已核验收入/投入资本比扩充白名单；保留全部既有观察ID、值、血缘及序列。
ALTER TABLE reference.industry_stat RENAME TO industry_stat_before_capital;
CREATE TABLE reference.industry_stat (
    observation_id BIGINT PRIMARY KEY DEFAULT nextval('reference.industry_stat_id_seq'),
    release_id BIGINT NOT NULL,
    artifact_id BIGINT NOT NULL,
    source_locator VARCHAR NOT NULL CHECK (length(trim(source_locator)) > 0),
    raw_value VARCHAR NOT NULL,
    raw_unit VARCHAR NOT NULL CHECK (length(trim(raw_unit)) > 0),
    industry_node_id BIGINT NOT NULL,
    sample_region VARCHAR NOT NULL CHECK (length(trim(sample_region)) > 0),
    observation_date DATE NOT NULL,
    metric_code VARCHAR NOT NULL CHECK (metric_code IN ('beta_unlevered', 'beta_unlevered_cash_adjusted', 'debt_equity_ratio', 'effective_tax_rate', 'sales_to_invested_capital_ltm')),
    method_code VARCHAR NOT NULL CHECK (length(trim(method_code)) > 0),
    statistic_code VARCHAR NOT NULL CHECK (length(trim(statistic_code)) > 0),
    sample_count INTEGER CHECK (sample_count >= 0),
    value DECIMAL(38,12),
    value_status VARCHAR NOT NULL CHECK (value_status IN ('reported', 'missing', 'not_applicable')),
    CHECK ((value_status = 'reported' AND value IS NOT NULL) OR (value_status <> 'reported' AND value IS NULL)),
    UNIQUE(release_id, industry_node_id, sample_region, observation_date, metric_code, method_code, statistic_code)
);

INSERT INTO reference.industry_stat SELECT * FROM reference.industry_stat_before_capital;
DROP TABLE reference.industry_stat_before_capital;
