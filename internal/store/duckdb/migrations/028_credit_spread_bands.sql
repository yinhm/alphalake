-- 月份标签用首日作载体，显式 month 精度；不是具体发布日期。
CREATE SEQUENCE reference.credit_spread_band_id_seq START 1;
CREATE TABLE reference.credit_spread_band (
 observation_id BIGINT PRIMARY KEY DEFAULT nextval('reference.credit_spread_band_id_seq'),
 release_id BIGINT NOT NULL,
 artifact_id BIGINT NOT NULL,
 source_locator VARCHAR NOT NULL CHECK(length(trim(source_locator))>0),
 observation_date DATE NOT NULL,
 observation_precision VARCHAR NOT NULL CHECK(observation_precision='month'),
 firm_type VARCHAR NOT NULL CHECK(firm_type='large_nonfinancial'),
 method_code VARCHAR NOT NULL CHECK(method_code='us_synthetic_rating_source_open_closed'),
 coverage_lower DECIMAL(38,12) NOT NULL,
 coverage_upper DECIMAL(38,12) NOT NULL,
 raw_lower VARCHAR NOT NULL,
 raw_upper VARCHAR NOT NULL,
 rating VARCHAR NOT NULL,
 value DECIMAL(38,12) NOT NULL CHECK(value>=0 AND value<1),
 raw_value VARCHAR NOT NULL,
 raw_unit VARCHAR NOT NULL CHECK(raw_unit='percent'),
 CHECK(coverage_lower<coverage_upper),
 UNIQUE(release_id,firm_type,rating),
 UNIQUE(release_id,firm_type,coverage_lower)
);
