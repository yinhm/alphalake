-- 仅建立存储结构；跨 schema 血缘、发布范围、白名单语义与不可变发布
-- 必须由后续写入事务校验。本迁移不提供同步器或 ASOF 选择器。
CREATE SEQUENCE meta.dataset_release_id_seq START 1;
CREATE TABLE meta.dataset_release (
    release_id BIGINT PRIMARY KEY DEFAULT nextval('meta.dataset_release_id_seq'),
    source VARCHAR NOT NULL CHECK (length(trim(source)) > 0),
    dataset VARCHAR NOT NULL CHECK (length(trim(dataset)) > 0),
    source_version VARCHAR,
    content_key VARCHAR NOT NULL CHECK (regexp_full_match(content_key, '[0-9a-f]{64}')),
    source_published_at TIMESTAMPTZ,
    publication_precision VARCHAR NOT NULL CHECK (publication_precision IN ('timestamp', 'date', 'unknown')),
    available_at TIMESTAMPTZ NOT NULL,
    availability_basis VARCHAR NOT NULL CHECK (availability_basis IN ('published_timestamp', 'published_date_boundary', 'first_seen')),
    first_seen_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    parser_version VARCHAR NOT NULL CHECK (length(trim(parser_version)) > 0),
    normalization_version VARCHAR NOT NULL CHECK (length(trim(normalization_version)) > 0),
    ingest_run_id BIGINT NOT NULL REFERENCES meta.ingest_run(ingest_run_id),
    supersedes_release_id BIGINT REFERENCES meta.dataset_release(release_id),
    UNIQUE(source, dataset, content_key),
    CHECK (supersedes_release_id IS NULL OR supersedes_release_id <> release_id),
    CHECK (recorded_at >= first_seen_at),
    CHECK (
        (availability_basis = 'first_seen' AND available_at = first_seen_at) OR
        (availability_basis = 'published_timestamp' AND publication_precision = 'timestamp'
         AND source_published_at IS NOT NULL AND available_at = source_published_at) OR
        (availability_basis = 'published_date_boundary' AND publication_precision = 'date'
         AND source_published_at IS NOT NULL AND available_at > source_published_at)
    ),
    CHECK ((publication_precision = 'unknown' AND source_published_at IS NULL) OR
           (publication_precision <> 'unknown' AND source_published_at IS NOT NULL))
);

CREATE TABLE meta.dataset_release_artifact (
    release_id BIGINT NOT NULL REFERENCES meta.dataset_release(release_id),
    artifact_id BIGINT NOT NULL REFERENCES meta.artifact(artifact_id),
    role VARCHAR NOT NULL CHECK (role IN ('data', 'publication', 'timing')),
    PRIMARY KEY(release_id, artifact_id, role)
);

CREATE SEQUENCE market.yield_curve_point_id_seq START 1;
CREATE TABLE market.yield_curve_point (
    observation_id BIGINT PRIMARY KEY DEFAULT nextval('market.yield_curve_point_id_seq'),
    release_id BIGINT NOT NULL,
    artifact_id BIGINT NOT NULL,
    source_locator VARCHAR NOT NULL CHECK (length(trim(source_locator)) > 0),
    raw_value VARCHAR NOT NULL,
    raw_unit VARCHAR NOT NULL CHECK (length(trim(raw_unit)) > 0),
    curve_code VARCHAR NOT NULL CHECK (length(trim(curve_code)) > 0),
    observation_date DATE NOT NULL,
    currency VARCHAR NOT NULL CHECK (regexp_full_match(currency, '[A-Z]{3}')),
    tenor_months INTEGER NOT NULL CHECK (tenor_months > 0),
    -- 远期曲线还需起始期限；首批不以单一期限字段伪装支持。
    rate_type VARCHAR NOT NULL CHECK (rate_type IN ('yield_to_maturity', 'spot')),
    compounding VARCHAR NOT NULL CHECK (length(trim(compounding)) > 0),
    day_count VARCHAR NOT NULL CHECK (length(trim(day_count)) > 0),
    value DECIMAL(38,12) NOT NULL,
    UNIQUE(release_id, curve_code, observation_date, currency, tenor_months, rate_type, compounding, day_count)
);

CREATE SEQUENCE market.fx_rate_id_seq START 1;
CREATE TABLE market.fx_rate (
    observation_id BIGINT PRIMARY KEY DEFAULT nextval('market.fx_rate_id_seq'),
    release_id BIGINT NOT NULL,
    artifact_id BIGINT NOT NULL,
    source_locator VARCHAR NOT NULL CHECK (length(trim(source_locator)) > 0),
    raw_value VARCHAR NOT NULL,
    raw_unit VARCHAR NOT NULL CHECK (length(trim(raw_unit)) > 0),
    base_currency VARCHAR NOT NULL CHECK (regexp_full_match(base_currency, '[A-Z]{3}')),
    quote_currency VARCHAR NOT NULL CHECK (regexp_full_match(quote_currency, '[A-Z]{3}')),
    observed_at TIMESTAMPTZ NOT NULL,
    time_precision VARCHAR NOT NULL CHECK (time_precision IN ('timestamp', 'date')),
    source_timezone VARCHAR NOT NULL CHECK (length(trim(source_timezone)) > 0),
    fixing_code VARCHAR NOT NULL CHECK (length(trim(fixing_code)) > 0),
    rate_type VARCHAR NOT NULL CHECK (rate_type IN ('midpoint', 'close', 'bid', 'ask')),
    value DECIMAL(38,12) NOT NULL CHECK (value > 0),
    CHECK (base_currency <> quote_currency),
    UNIQUE(release_id, base_currency, quote_currency, observed_at, fixing_code, rate_type)
);

CREATE SCHEMA IF NOT EXISTS reference;
CREATE SEQUENCE reference.country_risk_id_seq START 1;
CREATE TABLE reference.country_risk (
    observation_id BIGINT PRIMARY KEY DEFAULT nextval('reference.country_risk_id_seq'),
    release_id BIGINT NOT NULL,
    artifact_id BIGINT NOT NULL,
    source_locator VARCHAR NOT NULL CHECK (length(trim(source_locator)) > 0),
    raw_value VARCHAR NOT NULL,
    raw_unit VARCHAR NOT NULL CHECK (length(trim(raw_unit)) > 0),
    subject_kind VARCHAR NOT NULL CHECK (subject_kind IN ('country', 'market_group')),
    subject_code VARCHAR NOT NULL CHECK (length(trim(subject_code)) > 0),
    observation_date DATE NOT NULL,
    metric_code VARCHAR NOT NULL CHECK (metric_code IN ('mature_market_erp', 'country_risk_premium', 'total_equity_risk_premium', 'sovereign_default_spread')),
    method_code VARCHAR NOT NULL CHECK (length(trim(method_code)) > 0),
    value DECIMAL(38,12),
    value_status VARCHAR NOT NULL CHECK (value_status IN ('reported', 'missing', 'not_applicable')),
    CHECK ((value_status = 'reported' AND value IS NOT NULL) OR (value_status <> 'reported' AND value IS NULL)),
    CHECK ((metric_code = 'mature_market_erp' AND subject_kind = 'market_group') OR
           (metric_code <> 'mature_market_erp' AND subject_kind = 'country')),
    UNIQUE(release_id, subject_kind, subject_code, observation_date, metric_code, method_code)
);

CREATE SEQUENCE reference.industry_stat_id_seq START 1;
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
    metric_code VARCHAR NOT NULL CHECK (metric_code IN ('beta_unlevered', 'beta_unlevered_cash_adjusted', 'debt_equity_ratio', 'effective_tax_rate')),
    method_code VARCHAR NOT NULL CHECK (length(trim(method_code)) > 0),
    statistic_code VARCHAR NOT NULL CHECK (length(trim(statistic_code)) > 0),
    sample_count INTEGER CHECK (sample_count >= 0),
    value DECIMAL(38,12),
    value_status VARCHAR NOT NULL CHECK (value_status IN ('reported', 'missing', 'not_applicable')),
    CHECK ((value_status = 'reported' AND value IS NOT NULL) OR (value_status <> 'reported' AND value IS NULL)),
    UNIQUE(release_id, industry_node_id, sample_region, observation_date, metric_code, method_code, statistic_code)
);
