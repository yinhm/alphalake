CREATE SCHEMA macro;

CREATE TABLE macro.series (
    series_code VARCHAR PRIMARY KEY CHECK(regexp_full_match(series_code,'cn_[a-z0-9_]{2,100}')),
    name_zh VARCHAR NOT NULL CHECK(length(trim(name_zh))>0),
    source VARCHAR NOT NULL CHECK(source IN ('nbs','pbc','mof')),
    dataset VARCHAR NOT NULL,
    geography VARCHAR NOT NULL CHECK(geography='CN'),
    frequency VARCHAR NOT NULL CHECK(frequency IN ('monthly','quarterly')),
    period_basis VARCHAR NOT NULL CHECK(period_basis IN ('month','month_or_jan_feb','quarter','ytd','point_in_time')),
    unit VARCHAR NOT NULL CHECK(unit IN ('CNY','fraction','index_points','percentage_points','square_metre','CNY_per_person')),
    price_basis VARCHAR NOT NULL CHECK(price_basis IN ('current_prices','constant_prices','price_change','not_applicable')),
    seasonal_adjustment VARCHAR NOT NULL CHECK(seasonal_adjustment IN ('seasonally_adjusted','not_seasonally_adjusted','provider_unspecified')),
    definition VARCHAR NOT NULL CHECK(length(trim(definition))>0)
);

CREATE TABLE macro.observation (
    -- DuckDB has no cross-schema foreign keys; the publication transaction verifies this lineage.
    release_id BIGINT NOT NULL CHECK(release_id>0),
    series_code VARCHAR NOT NULL REFERENCES macro.series(series_code),
    period_start DATE NOT NULL,
    period_end DATE NOT NULL CHECK(period_end>=period_start),
    value DECIMAL(38,12),
    value_status VARCHAR NOT NULL CHECK(value_status IN ('reported','not_reported')),
    raw_value VARCHAR NOT NULL,
    raw_unit VARCHAR NOT NULL,
    source_locator VARCHAR NOT NULL,
    PRIMARY KEY(release_id,series_code,period_end),
    CHECK((value_status='reported' AND value IS NOT NULL) OR (value_status='not_reported' AND value IS NULL))
);
