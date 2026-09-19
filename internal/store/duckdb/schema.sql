CREATE SCHEMA classification;
CREATE SCHEMA core;
CREATE SCHEMA fundamental;
CREATE SCHEMA market;
CREATE SCHEMA meta;
CREATE SCHEMA reference;
CREATE SEQUENCE classification.node_id_seq INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 NO CYCLE;
CREATE SEQUENCE classification.taxonomy_id_seq INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 NO CYCLE;
CREATE SEQUENCE core.company_id_seq INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 NO CYCLE;
CREATE SEQUENCE core.instrument_identifier_id_seq INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 NO CYCLE;
CREATE SEQUENCE core.instrument_id_seq INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 NO CYCLE;
CREATE SEQUENCE core.listing_identifier_id_seq INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 NO CYCLE;
CREATE SEQUENCE core.listing_id_seq INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 NO CYCLE;
CREATE SEQUENCE fundamental.fact_id_seq INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 NO CYCLE;
CREATE SEQUENCE fundamental.filing_id_seq INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 NO CYCLE;
CREATE SEQUENCE fundamental.provider_fact_id_seq INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 NO CYCLE;
CREATE SEQUENCE market.corporate_action_id_seq INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 NO CYCLE;
CREATE SEQUENCE market.daily_observation_id_seq INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 NO CYCLE;
CREATE SEQUENCE market.equity_proceeds_id_seq INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 NO CYCLE;
CREATE SEQUENCE market.fx_rate_id_seq INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 NO CYCLE;
CREATE SEQUENCE market.listing_close_id_seq INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 NO CYCLE;
CREATE SEQUENCE market.share_count_id_seq INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 NO CYCLE;
CREATE SEQUENCE market.yield_curve_point_id_seq INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 NO CYCLE;
CREATE SEQUENCE meta.artifact_id_seq INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 NO CYCLE;
CREATE SEQUENCE meta.dataset_release_id_seq INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 NO CYCLE;
CREATE SEQUENCE meta.ingest_run_id_seq INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 NO CYCLE;
CREATE SEQUENCE meta.validation_result_id_seq INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 NO CYCLE;
CREATE SEQUENCE reference.country_risk_id_seq INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 NO CYCLE;
CREATE SEQUENCE reference.credit_spread_band_id_seq INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 NO CYCLE;
CREATE SEQUENCE reference.industry_stat_id_seq INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 NO CYCLE;
CREATE SEQUENCE reference.security_code_transition_id_seq INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 NO CYCLE;
CREATE SEQUENCE reference.security_industry_id_seq INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 NO CYCLE;
CREATE TABLE classification.membership(instrument_id BIGINT, node_id BIGINT, effective_from DATE, effective_to DATE, "source" VARCHAR, observed_at TIMESTAMP WITH TIME ZONE NOT NULL, artifact_id BIGINT, ingest_run_id BIGINT, last_observed_at TIMESTAMP WITH TIME ZONE, last_observed_run_id BIGINT, PRIMARY KEY(instrument_id, node_id, effective_from, "source"));
CREATE TABLE classification.node(node_id BIGINT DEFAULT(nextval('classification.node_id_seq')) PRIMARY KEY, taxonomy_id BIGINT NOT NULL, source_node_code VARCHAR NOT NULL, "name" VARCHAR NOT NULL, parent_node_id BIGINT, "level" INTEGER, source_symbol VARCHAR, UNIQUE(taxonomy_id, source_node_code));
CREATE TABLE classification.taxonomy(taxonomy_id BIGINT DEFAULT(nextval('classification.taxonomy_id_seq')) PRIMARY KEY, "source" VARCHAR NOT NULL, taxonomy_code VARCHAR NOT NULL, "name" VARCHAR NOT NULL, taxonomy_type VARCHAR NOT NULL, UNIQUE("source", taxonomy_code));
CREATE TABLE core.company(company_id BIGINT DEFAULT(nextval('core.company_id_seq')) PRIMARY KEY, legal_name VARCHAR, short_name VARCHAR, country_code VARCHAR);
CREATE TABLE core.exchange(mic VARCHAR PRIMARY KEY, "name" VARCHAR NOT NULL, country_code VARCHAR, timezone VARCHAR NOT NULL, currency VARCHAR);
CREATE TABLE core.instrument(instrument_id BIGINT DEFAULT(nextval('core.instrument_id_seq')) PRIMARY KEY, instrument_type VARCHAR NOT NULL, exchange_mic VARCHAR, currency VARCHAR, company_id BIGINT, "name" VARCHAR, list_date DATE, delist_date DATE, status VARCHAR DEFAULT('active') NOT NULL, created_at TIMESTAMP WITH TIME ZONE DEFAULT(current_timestamp) NOT NULL, updated_at TIMESTAMP WITH TIME ZONE DEFAULT(current_timestamp) NOT NULL);
CREATE TABLE core.instrument_identifier(instrument_identifier_id BIGINT DEFAULT(nextval('core.instrument_identifier_id_seq')) PRIMARY KEY, instrument_id BIGINT NOT NULL, provider VARCHAR NOT NULL, identifier_type VARCHAR NOT NULL, identifier_value VARCHAR NOT NULL, valid_from DATE, valid_to DATE, is_primary BOOLEAN DEFAULT(CAST('f' AS BOOLEAN)) NOT NULL, UNIQUE(provider, identifier_type, identifier_value, valid_from));
CREATE TABLE fundamental.document_review(filing_id BIGINT, document_artifact_id BIGINT, review_artifact_id BIGINT NOT NULL, pdf_sha256 VARCHAR NOT NULL, reviewed_at TIMESTAMP WITH TIME ZONE NOT NULL, recorded_at TIMESTAMP WITH TIME ZONE, reviewed_record VARCHAR NOT NULL, CHECK(json_valid(reviewed_record)), PRIMARY KEY(filing_id, document_artifact_id));
CREATE TABLE fundamental.fact(fact_id BIGINT DEFAULT(nextval('fundamental.fact_id_seq')) PRIMARY KEY, instrument_id BIGINT NOT NULL, canonical_field VARCHAR NOT NULL, report_period DATE NOT NULL, announcement_time TIMESTAMP WITH TIME ZONE NOT NULL, period_type VARCHAR NOT NULL, statement_scope VARCHAR NOT NULL, currency VARCHAR, unit VARCHAR NOT NULL, "value" DECIMAL(38,10) NOT NULL, primary_source VARCHAR NOT NULL, source_provider_field VARCHAR NOT NULL, provider_code VARCHAR, provider_fact_id BIGINT, source_filing_id BIGINT NOT NULL, revision_key VARCHAR NOT NULL, normalization_rule VARCHAR NOT NULL, materializer_version VARCHAR NOT NULL, ingest_run_id BIGINT, ingested_at TIMESTAMP WITH TIME ZONE DEFAULT(current_timestamp) NOT NULL, UNIQUE(primary_source, revision_key, provider_code, source_provider_field));
CREATE TABLE fundamental.field(canonical_field VARCHAR PRIMARY KEY, unit VARCHAR NOT NULL, value_kind VARCHAR NOT NULL, period_basis VARCHAR NOT NULL, CHECK((period_basis IN ('instant', 'quarter', 'ytd', 'opening_instant'))));
CREATE TABLE fundamental.filing(filing_id BIGINT DEFAULT(nextval('fundamental.filing_id_seq')) PRIMARY KEY, instrument_id BIGINT, "source" VARCHAR NOT NULL, source_filing_id VARCHAR NOT NULL, provider_code VARCHAR DEFAULT('') NOT NULL, exchange_mic VARCHAR, security_name VARCHAR, filing_type VARCHAR, filing_variant VARCHAR DEFAULT('other') NOT NULL, report_period DATE, announcement_time TIMESTAMP WITH TIME ZONE, title VARCHAR, source_url VARCHAR, raw_category VARCHAR, classifier_version VARCHAR DEFAULT('legacy') NOT NULL, is_correction BOOLEAN DEFAULT(CAST('f' AS BOOLEAN)) NOT NULL, corrects_filing_id BIGINT, resolution_status VARCHAR DEFAULT('resolved') NOT NULL, resolution_reason VARCHAR, catalogue_artifact_id BIGINT, artifact_id BIGINT, sha256 VARCHAR, provider_org_id VARCHAR, provider_column_id VARCHAR, provider_page_column VARCHAR, raw_announcement_time_ms BIGINT, ingest_run_id BIGINT, first_seen_at TIMESTAMP WITH TIME ZONE DEFAULT(current_timestamp) NOT NULL, last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT(current_timestamp) NOT NULL, ingested_at TIMESTAMP WITH TIME ZONE DEFAULT(current_timestamp) NOT NULL, announcement_date DATE, announcement_time_precision VARCHAR DEFAULT('timestamp'), UNIQUE("source", source_filing_id), CHECK((resolution_status IN ('resolved', 'pending', 'acknowledged'))));
CREATE TABLE fundamental.filing_document(filing_id BIGINT, artifact_id BIGINT, source_url VARCHAR NOT NULL, sha256 VARCHAR NOT NULL, fetched_at TIMESTAMP WITH TIME ZONE NOT NULL, ingest_run_id BIGINT, PRIMARY KEY(filing_id, artifact_id));
CREATE TABLE fundamental.provider_fact(provider_fact_id BIGINT DEFAULT(nextval('fundamental.provider_fact_id_seq')) PRIMARY KEY, instrument_id BIGINT NOT NULL, "source" VARCHAR NOT NULL, report_period DATE NOT NULL, announcement_time TIMESTAMP WITH TIME ZONE, provider_code VARCHAR, market_marker USMALLINT, provider_field VARCHAR NOT NULL, "value" DOUBLE, value_float32_bits UBIGINT, source_file VARCHAR, source_file_hash VARCHAR, artifact_id BIGINT, ingest_run_id BIGINT, revision_key VARCHAR DEFAULT('') NOT NULL, ingested_at TIMESTAMP WITH TIME ZONE DEFAULT(current_timestamp) NOT NULL, UNIQUE("source", revision_key, provider_code, provider_field));
CREATE TABLE fundamental.provider_field("source" VARCHAR, provider_field VARCHAR, canonical_field VARCHAR, display_name VARCHAR, unit VARCHAR, value_kind VARCHAR, valid_from DATE DEFAULT(CAST('1900-01-01' AS "DATE")), valid_to DATE, notes VARCHAR, period_basis VARCHAR DEFAULT('report'), value_multiplier INTEGER DEFAULT(1), zero_policy VARCHAR DEFAULT('reject') NOT NULL CHECK(zero_policy IN ('allow','reject')), PRIMARY KEY("source", provider_field, valid_from));
CREATE TABLE fundamental.provider_filing_link(provider_source VARCHAR, provider_revision_key VARCHAR, provider_artifact_id BIGINT NOT NULL, provider_code VARCHAR, report_period DATE NOT NULL, instrument_id BIGINT, filing_id BIGINT, status VARCHAR NOT NULL, candidate_count INTEGER DEFAULT(0) NOT NULL, link_method VARCHAR, reason VARCHAR, linker_version VARCHAR NOT NULL, linked_at TIMESTAMP WITH TIME ZONE, updated_at TIMESTAMP WITH TIME ZONE DEFAULT(current_timestamp) NOT NULL, ingest_run_id BIGINT, PRIMARY KEY(provider_source, provider_revision_key, provider_code));
CREATE TABLE fundamental.provider_record_resolution(artifact_id BIGINT, "source" VARCHAR NOT NULL, source_file VARCHAR NOT NULL, report_period DATE NOT NULL, provider_code VARCHAR, market_marker USMALLINT NOT NULL, status VARCHAR NOT NULL, instrument_id BIGINT, identifier_value VARCHAR, reason VARCHAR, acknowledged_reason VARCHAR, acknowledged_at TIMESTAMP WITH TIME ZONE, last_ingest_run_id BIGINT, updated_at TIMESTAMP WITH TIME ZONE DEFAULT(now()) NOT NULL, PRIMARY KEY(artifact_id, provider_code), CHECK((status IN ('resolved', 'pending', 'acknowledged'))));
CREATE TABLE fundamental.reviewed_supplement(provider_code VARCHAR, report_period DATE, item VARCHAR, "value" DECIMAL(38,10) NOT NULL, unit VARCHAR NOT NULL, period_basis VARCHAR NOT NULL, statement_scope VARCHAR NOT NULL, source_filing_id BIGINT, pdf_sha256 VARCHAR NOT NULL, pdf_page INTEGER NOT NULL, reviewer VARCHAR NOT NULL, review_note VARCHAR NOT NULL, import_sha256 VARCHAR NOT NULL, reviewed_record VARCHAR NOT NULL, review_state VARCHAR DEFAULT('active'), CHECK((unit IN ('CNY', 'CNY/share'))), CHECK((period_basis IN ('instant', 'ytd'))), CHECK((pdf_page > 0)), PRIMARY KEY(provider_code, report_period, item, source_filing_id));
CREATE TABLE fundamental.supplement_review_history(import_sha256 VARCHAR PRIMARY KEY, provider_code VARCHAR NOT NULL, report_period DATE NOT NULL, item VARCHAR NOT NULL, source_filing_id BIGINT NOT NULL, "action" VARCHAR NOT NULL, supersedes_sha256 VARCHAR, reviewed_at TIMESTAMP WITH TIME ZONE, recorded_at TIMESTAMP WITH TIME ZONE, reviewed_record VARCHAR NOT NULL, CHECK(("action" IN ('publish', 'replace', 'revoke'))), CHECK(json_valid(reviewed_record)), CHECK(((("action" = 'publish') AND (supersedes_sha256 IS NULL)) OR (("action" != 'publish') AND (supersedes_sha256 IS NOT NULL)))));
CREATE TABLE market.adjustment_segment(instrument_id BIGINT, effective_from DATE, effective_to DATE, qfq_mul DOUBLE NOT NULL, qfq_add DOUBLE NOT NULL, hfq_mul DOUBLE NOT NULL, hfq_add DOUBLE NOT NULL, "method" VARCHAR, "source" VARCHAR, calculated_at TIMESTAMP WITH TIME ZONE DEFAULT(current_timestamp) NOT NULL, ingest_run_id BIGINT, PRIMARY KEY(instrument_id, effective_from, "method", "source"));
CREATE TABLE market.corporate_action(corporate_action_id BIGINT DEFAULT(nextval('market.corporate_action_id_seq')) PRIMARY KEY, instrument_id BIGINT NOT NULL, action_date DATE NOT NULL, action_type VARCHAR NOT NULL, source_category INTEGER, cash_dividend_per_10 DECIMAL(30,10), rights_price DECIMAL(30,10), bonus_or_split_per_10 DECIMAL(30,10), rights_per_10 DECIMAL(30,10), scale_factor DECIMAL(30,12), raw_c1 DOUBLE, raw_c2 DOUBLE, raw_c3 DOUBLE, raw_c4 DOUBLE, "source" VARCHAR NOT NULL, source_record_id VARCHAR, artifact_id BIGINT, ingest_run_id BIGINT, UNIQUE(instrument_id, action_date, "source", source_category, source_record_id));
CREATE TABLE market.daily_observation(observation_id BIGINT DEFAULT(nextval('market.daily_observation_id_seq')) PRIMARY KEY, instrument_id BIGINT NOT NULL, trade_date DATE NOT NULL, open DECIMAL(20,6), high DECIMAL(20,6), low DECIMAL(20,6), "close" DECIMAL(20,6), volume BIGINT, amount DECIMAL(30,6), up_count BIGINT, down_count BIGINT, "source" VARCHAR NOT NULL, ingest_run_id BIGINT NOT NULL, recorded_at TIMESTAMP WITH TIME ZONE DEFAULT(current_timestamp) NOT NULL);
CREATE TABLE market.equity_proceeds_observation(observation_id BIGINT DEFAULT(nextval('market.equity_proceeds_id_seq')) PRIMARY KEY, release_id BIGINT NOT NULL, artifact_id BIGINT NOT NULL, company_id BIGINT NOT NULL, event_code VARCHAR NOT NULL, listing_date DATE NOT NULL, date_status VARCHAR NOT NULL, issued_shares DECIMAL(38,0) NOT NULL, currency VARCHAR NOT NULL, net_proceeds DECIMAL(38,10) NOT NULL, amount_status VARCHAR NOT NULL, source_locator VARCHAR NOT NULL, CHECK((event_code IN ('ipo', 'greenshoe'))), CHECK((date_status IN ('reported_listing_date_not_cash_settlement', 'expected_listing_date_not_cash_settlement'))), CHECK((issued_shares > 0)), CHECK(regexp_full_match(currency, '[A-Z]{3}')), CHECK((net_proceeds > 0)), CHECK((amount_status = 'issuer_estimate_after_estimated_costs')), CHECK((length(main."trim"(source_locator)) > 0)), UNIQUE(release_id, event_code));
CREATE TABLE market.fx_rate(observation_id BIGINT DEFAULT(nextval('market.fx_rate_id_seq')) PRIMARY KEY, release_id BIGINT NOT NULL, artifact_id BIGINT NOT NULL, source_locator VARCHAR NOT NULL, raw_value VARCHAR NOT NULL, raw_unit VARCHAR NOT NULL, base_currency VARCHAR NOT NULL, quote_currency VARCHAR NOT NULL, observed_at TIMESTAMP WITH TIME ZONE NOT NULL, time_precision VARCHAR NOT NULL, source_timezone VARCHAR NOT NULL, fixing_code VARCHAR NOT NULL, rate_type VARCHAR NOT NULL, "value" DECIMAL(38,12) NOT NULL, CHECK((length(main."trim"(source_locator)) > 0)), CHECK((length(main."trim"(raw_unit)) > 0)), CHECK(regexp_full_match(base_currency, '[A-Z]{3}')), CHECK(regexp_full_match(quote_currency, '[A-Z]{3}')), CHECK((time_precision IN ('timestamp', 'date'))), CHECK((length(main."trim"(source_timezone)) > 0)), CHECK((length(main."trim"(fixing_code)) > 0)), CHECK((rate_type IN ('midpoint', 'close', 'bid', 'ask'))), CHECK(("value" > 0)), CHECK((base_currency != quote_currency)), UNIQUE(release_id, base_currency, quote_currency, observed_at, fixing_code, rate_type));
CREATE TABLE market.listing_close_observation(observation_id BIGINT DEFAULT(nextval('market.listing_close_id_seq')) PRIMARY KEY, release_id BIGINT NOT NULL, artifact_id BIGINT NOT NULL, listing_id BIGINT NOT NULL, trade_date DATE NOT NULL, "close" DECIMAL(20,6) NOT NULL, raw_value VARCHAR NOT NULL, source_locator VARCHAR NOT NULL, adjustment VARCHAR NOT NULL, CHECK(("close" > 0)), CHECK((adjustment = 'unadjusted')), UNIQUE(release_id, listing_id, trade_date));
CREATE TABLE market.ohlcv_daily(instrument_id BIGINT, trade_date DATE, open DECIMAL(20,6), high DECIMAL(20,6), low DECIMAL(20,6), "close" DECIMAL(20,6), volume BIGINT, amount DECIMAL(30,6), up_count BIGINT, down_count BIGINT, "source" VARCHAR, source_record_id VARCHAR, artifact_id BIGINT, ingest_run_id BIGINT, ingested_at TIMESTAMP WITH TIME ZONE DEFAULT(current_timestamp) NOT NULL, PRIMARY KEY(instrument_id, trade_date, "source"));
CREATE TABLE market.share_capital(instrument_id BIGINT, effective_date DATE, float_shares BIGINT, total_shares BIGINT, source_category INTEGER, "source" VARCHAR, source_record_id VARCHAR, artifact_id BIGINT, ingest_run_id BIGINT, PRIMARY KEY(instrument_id, effective_date, "source", source_category, source_record_id));
CREATE TABLE market.share_count_observation(observation_id BIGINT DEFAULT(nextval('market.share_count_id_seq')) PRIMARY KEY, release_id BIGINT NOT NULL, artifact_id BIGINT NOT NULL, company_id BIGINT, instrument_id BIGINT, "scope" VARCHAR NOT NULL, share_basis VARCHAR NOT NULL, effective_date DATE NOT NULL, "value" DECIMAL(38,10) NOT NULL, source_locator VARCHAR NOT NULL, CHECK(("scope" IN ('company_total', 'share_class'))), CHECK((share_basis IN ('issued', 'treasury', 'outstanding', 'free_float'))), CHECK(("value" >= 0)), CHECK((length(main."trim"(source_locator)) > 0)), CHECK(((("scope" = 'company_total') AND (company_id IS NOT NULL) AND (instrument_id IS NULL)) OR (("scope" = 'share_class') AND (company_id IS NULL) AND (instrument_id IS NOT NULL)))), UNIQUE(release_id, instrument_id, share_basis, effective_date), UNIQUE(release_id, company_id, share_basis, effective_date));
CREATE TABLE market.trading_calendar(exchange_mic VARCHAR, trade_date DATE, is_open BOOLEAN NOT NULL, session_open TIME, session_close TIME, "source" VARCHAR, PRIMARY KEY(exchange_mic, trade_date, "source"));
CREATE TABLE market.yield_curve_point(observation_id BIGINT DEFAULT(nextval('market.yield_curve_point_id_seq')) PRIMARY KEY, release_id BIGINT NOT NULL, artifact_id BIGINT NOT NULL, source_locator VARCHAR NOT NULL, raw_value VARCHAR NOT NULL, raw_unit VARCHAR NOT NULL, curve_code VARCHAR NOT NULL, observation_date DATE NOT NULL, currency VARCHAR NOT NULL, tenor_months INTEGER NOT NULL, rate_type VARCHAR NOT NULL, compounding VARCHAR NOT NULL, day_count VARCHAR NOT NULL, "value" DECIMAL(38,12) NOT NULL, CHECK((length(main."trim"(source_locator)) > 0)), CHECK((length(main."trim"(raw_unit)) > 0)), CHECK((length(main."trim"(curve_code)) > 0)), CHECK(regexp_full_match(currency, '[A-Z]{3}')), CHECK((tenor_months > 0)), CHECK((rate_type IN ('yield_to_maturity', 'spot'))), CHECK((length(main."trim"(compounding)) > 0)), CHECK((length(main."trim"(day_count)) > 0)), UNIQUE(release_id, curve_code, observation_date, currency, tenor_months, rate_type, compounding, day_count));
CREATE TABLE meta.artifact(artifact_id BIGINT DEFAULT(nextval('meta.artifact_id_seq')) PRIMARY KEY, "source" VARCHAR NOT NULL, dataset VARCHAR NOT NULL, source_locator VARCHAR NOT NULL, fetched_at TIMESTAMP WITH TIME ZONE NOT NULL, sha256 VARCHAR NOT NULL, content_length BIGINT NOT NULL, media_type VARCHAR, local_path VARCHAR, parser_version VARCHAR, ingest_run_id BIGINT, UNIQUE("source", dataset, source_locator, sha256));
CREATE TABLE meta."checkpoint"("source" VARCHAR, dataset VARCHAR, checkpoint_key VARCHAR, checkpoint_value VARCHAR NOT NULL, updated_at TIMESTAMP WITH TIME ZONE DEFAULT(current_timestamp) NOT NULL, PRIMARY KEY("source", dataset, checkpoint_key));
CREATE TABLE meta.derived_state(dataset VARCHAR, instrument_id BIGINT, "source" VARCHAR, "method" VARCHAR, input_signature VARCHAR NOT NULL, output_ingest_run_id BIGINT NOT NULL, calculated_at TIMESTAMP WITH TIME ZONE DEFAULT(current_timestamp) NOT NULL, PRIMARY KEY(dataset, instrument_id, "source", "method"));
CREATE TABLE meta.ingest_run(ingest_run_id BIGINT DEFAULT(nextval('meta.ingest_run_id_seq')) PRIMARY KEY, "source" VARCHAR NOT NULL, dataset VARCHAR NOT NULL, started_at TIMESTAMP WITH TIME ZONE DEFAULT(current_timestamp) NOT NULL, finished_at TIMESTAMP WITH TIME ZONE, status VARCHAR NOT NULL, checkpoint_before VARCHAR, checkpoint_after VARCHAR, error_message VARCHAR);
CREATE TABLE meta.schema_version("version" INTEGER PRIMARY KEY, applied_at TIMESTAMP WITH TIME ZONE DEFAULT(current_timestamp) NOT NULL, description VARCHAR NOT NULL);
CREATE TABLE meta.validation_result(validation_result_id BIGINT DEFAULT(nextval('meta.validation_result_id_seq')) PRIMARY KEY, ingest_run_id BIGINT, "source" VARCHAR NOT NULL, dataset VARCHAR NOT NULL, rule_code VARCHAR NOT NULL, severity VARCHAR NOT NULL, subject_type VARCHAR, subject_key VARCHAR, observed_value VARCHAR, expected_value VARCHAR, passed BOOLEAN NOT NULL, details VARCHAR, checked_at TIMESTAMP WITH TIME ZONE DEFAULT(current_timestamp) NOT NULL);
CREATE TABLE reference.country_risk(observation_id BIGINT DEFAULT(nextval('reference.country_risk_id_seq')) PRIMARY KEY, release_id BIGINT NOT NULL, artifact_id BIGINT NOT NULL, source_locator VARCHAR NOT NULL, raw_value VARCHAR NOT NULL, raw_unit VARCHAR NOT NULL, subject_kind VARCHAR NOT NULL, subject_code VARCHAR NOT NULL, observation_date DATE NOT NULL, metric_code VARCHAR NOT NULL, method_code VARCHAR NOT NULL, "value" DECIMAL(38,12), value_status VARCHAR NOT NULL, CHECK((length(main."trim"(source_locator)) > 0)), CHECK((length(main."trim"(raw_unit)) > 0)), CHECK((subject_kind IN ('country'))), CHECK((length(main."trim"(subject_code)) > 0)), CHECK((metric_code IN ('country_risk_premium', 'sovereign_default_spread'))), CHECK((length(main."trim"(method_code)) > 0)), CHECK((value_status IN ('reported', 'missing', 'not_applicable'))), CHECK((((value_status = 'reported') AND ("value" IS NOT NULL)) OR ((value_status != 'reported') AND ("value" IS NULL)))), UNIQUE(release_id, subject_kind, subject_code, observation_date, metric_code, method_code));
CREATE TABLE reference.credit_spread_band(observation_id BIGINT DEFAULT(nextval('reference.credit_spread_band_id_seq')) PRIMARY KEY, release_id BIGINT NOT NULL, artifact_id BIGINT NOT NULL, source_locator VARCHAR NOT NULL, observation_date DATE NOT NULL, observation_precision VARCHAR NOT NULL, firm_type VARCHAR NOT NULL, method_code VARCHAR NOT NULL, coverage_lower DECIMAL(38,12) NOT NULL, coverage_upper DECIMAL(38,12) NOT NULL, raw_lower VARCHAR NOT NULL, raw_upper VARCHAR NOT NULL, rating VARCHAR NOT NULL, "value" DECIMAL(38,12) NOT NULL, raw_value VARCHAR NOT NULL, raw_unit VARCHAR NOT NULL, CHECK((length(main."trim"(source_locator)) > 0)), CHECK((observation_precision = 'month')), CHECK((firm_type = 'large_nonfinancial')), CHECK((method_code = 'us_synthetic_rating_source_open_closed')), CHECK((("value" >= 0) AND ("value" < 1))), CHECK((raw_unit = 'percent')), CHECK((coverage_lower < coverage_upper)), UNIQUE(release_id, firm_type, rating), UNIQUE(release_id, firm_type, coverage_lower));
CREATE TABLE reference.equity_risk_premium(observation_id BIGINT DEFAULT(nextval('reference.country_risk_id_seq')) PRIMARY KEY, release_id BIGINT NOT NULL, artifact_id BIGINT NOT NULL, source_locator VARCHAR NOT NULL, raw_value VARCHAR NOT NULL, raw_unit VARCHAR NOT NULL, subject_kind VARCHAR NOT NULL, subject_code VARCHAR NOT NULL, observation_date DATE NOT NULL, metric_code VARCHAR NOT NULL, method_code VARCHAR NOT NULL, "value" DECIMAL(38,12), value_status VARCHAR NOT NULL, CHECK((length(main."trim"(source_locator)) > 0)), CHECK((length(main."trim"(raw_unit)) > 0)), CHECK((subject_kind IN ('country', 'market_group'))), CHECK((length(main."trim"(subject_code)) > 0)), CHECK((metric_code IN ('mature_market_erp', 'total_equity_risk_premium'))), CHECK((length(main."trim"(method_code)) > 0)), CHECK((value_status IN ('reported', 'missing', 'not_applicable'))), CHECK((((value_status = 'reported') AND ("value" IS NOT NULL)) OR ((value_status != 'reported') AND ("value" IS NULL)))), CHECK((((metric_code = 'mature_market_erp') AND (subject_kind = 'market_group')) OR ((metric_code != 'mature_market_erp') AND (subject_kind = 'country')))), UNIQUE(release_id, subject_kind, subject_code, observation_date, metric_code, method_code));
CREATE TABLE reference.industry_stat(observation_id BIGINT DEFAULT(nextval('reference.industry_stat_id_seq')) PRIMARY KEY, release_id BIGINT NOT NULL, artifact_id BIGINT NOT NULL, source_locator VARCHAR NOT NULL, raw_value VARCHAR NOT NULL, raw_unit VARCHAR NOT NULL, industry_node_id BIGINT NOT NULL, sample_region VARCHAR NOT NULL, observation_date DATE NOT NULL, metric_code VARCHAR NOT NULL, method_code VARCHAR NOT NULL, statistic_code VARCHAR NOT NULL, sample_count INTEGER, "value" DECIMAL(38,12), value_status VARCHAR NOT NULL, CHECK((length(main."trim"(source_locator)) > 0)), CHECK((length(main."trim"(raw_unit)) > 0)), CHECK((length(main."trim"(sample_region)) > 0)), CHECK((metric_code IN ('beta_unlevered', 'beta_unlevered_cash_adjusted', 'debt_equity_ratio', 'effective_tax_rate', 'sales_to_invested_capital_ltm'))), CHECK((length(main."trim"(method_code)) > 0)), CHECK((length(main."trim"(statistic_code)) > 0)), CHECK((sample_count >= 0)), CHECK((value_status IN ('reported', 'missing', 'not_applicable'))), CHECK((((value_status = 'reported') AND ("value" IS NOT NULL)) OR ((value_status != 'reported') AND ("value" IS NULL)))), UNIQUE(release_id, industry_node_id, sample_region, observation_date, metric_code, method_code, statistic_code));
CREATE TABLE reference.security_code_transition(observation_id BIGINT DEFAULT(nextval('reference.security_code_transition_id_seq')) PRIMARY KEY, release_id BIGINT NOT NULL, artifact_id BIGINT NOT NULL, source_row INTEGER NOT NULL, exchange_mic VARCHAR NOT NULL, old_code VARCHAR NOT NULL, new_code VARCHAR NOT NULL, source_name VARCHAR NOT NULL, source_listing_date VARCHAR NOT NULL, switch_date DATE NOT NULL, CHECK((source_row > 0)), CHECK((exchange_mic = 'XBSE')), CHECK(regexp_full_match(old_code, '[0-9]{6}')), CHECK((regexp_full_match(new_code, '920[0-9]{3}') AND (new_code != old_code))), CHECK((length(main."trim"(source_name)) > 0)), UNIQUE(release_id, old_code), UNIQUE(release_id, new_code), UNIQUE(release_id, source_row));
CREATE TABLE reference.security_industry(observation_id BIGINT DEFAULT(nextval('reference.security_industry_id_seq')) PRIMARY KEY, release_id BIGINT NOT NULL, artifact_id BIGINT NOT NULL, source_locator VARCHAR NOT NULL, exchange_ticker VARCHAR NOT NULL, industry_node_id BIGINT NOT NULL, raw_payload VARCHAR NOT NULL, CHECK((length(main."trim"(source_locator)) > 0)), CHECK((length(main."trim"(exchange_ticker)) > 0)), CHECK(json_valid(raw_payload)), UNIQUE(release_id, exchange_ticker), UNIQUE(release_id, source_locator));
CREATE TABLE core.listing(listing_id BIGINT DEFAULT(nextval('core.listing_id_seq')) PRIMARY KEY, instrument_id BIGINT NOT NULL, exchange_mic VARCHAR NOT NULL, trading_currency VARCHAR NOT NULL, valid_from DATE NOT NULL, valid_to DATE, artifact_id BIGINT NOT NULL, recorded_at TIMESTAMP WITH TIME ZONE DEFAULT(current_timestamp) NOT NULL, FOREIGN KEY (instrument_id) REFERENCES core.instrument(instrument_id), CHECK((exchange_mic IN ('XSHG', 'XSHE', 'XHKG'))), CHECK((trading_currency IN ('CNY', 'HKD'))), CHECK(((valid_to IS NULL) OR (valid_to > valid_from))), UNIQUE(instrument_id, exchange_mic, trading_currency, valid_from));
CREATE TABLE core.listing_identifier(listing_identifier_id BIGINT DEFAULT(nextval('core.listing_identifier_id_seq')) PRIMARY KEY, listing_id BIGINT NOT NULL, provider VARCHAR NOT NULL, identifier_type VARCHAR NOT NULL, identifier_value VARCHAR NOT NULL, market_namespace VARCHAR NOT NULL, valid_from DATE NOT NULL, valid_to DATE, artifact_id BIGINT NOT NULL, recorded_at TIMESTAMP WITH TIME ZONE DEFAULT(current_timestamp) NOT NULL, FOREIGN KEY (listing_id) REFERENCES core.listing(listing_id), CHECK(((valid_to IS NULL) OR (valid_to > valid_from))), UNIQUE(provider, identifier_type, market_namespace, identifier_value, valid_from));
CREATE TABLE meta.dataset_release(release_id BIGINT DEFAULT(nextval('meta.dataset_release_id_seq')) PRIMARY KEY, "source" VARCHAR NOT NULL, dataset VARCHAR NOT NULL, source_version VARCHAR, content_key VARCHAR NOT NULL, source_published_at TIMESTAMP WITH TIME ZONE, publication_precision VARCHAR NOT NULL, available_at TIMESTAMP WITH TIME ZONE NOT NULL, availability_basis VARCHAR NOT NULL, first_seen_at TIMESTAMP WITH TIME ZONE NOT NULL, recorded_at TIMESTAMP WITH TIME ZONE DEFAULT(current_timestamp) NOT NULL, parser_version VARCHAR NOT NULL, normalization_version VARCHAR NOT NULL, ingest_run_id BIGINT NOT NULL, supersedes_release_id BIGINT, CHECK((length(main."trim"("source")) > 0)), CHECK((length(main."trim"(dataset)) > 0)), CHECK(regexp_full_match(content_key, '[0-9a-f]{64}')), CHECK((publication_precision IN ('timestamp', 'date', 'unknown'))), CHECK((availability_basis IN ('published_timestamp', 'published_date_boundary', 'first_seen'))), CHECK((length(main."trim"(parser_version)) > 0)), CHECK((length(main."trim"(normalization_version)) > 0)), FOREIGN KEY (ingest_run_id) REFERENCES meta.ingest_run(ingest_run_id), FOREIGN KEY (supersedes_release_id) REFERENCES meta.dataset_release(release_id), UNIQUE("source", dataset, content_key), CHECK(((supersedes_release_id IS NULL) OR (supersedes_release_id != release_id))), CHECK((recorded_at >= first_seen_at)), CHECK((((availability_basis = 'first_seen') AND (available_at = first_seen_at)) OR ((availability_basis = 'published_timestamp') AND (publication_precision = 'timestamp') AND (source_published_at IS NOT NULL) AND (available_at = source_published_at)) OR ((availability_basis = 'published_date_boundary') AND (publication_precision = 'date') AND (source_published_at IS NOT NULL) AND (available_at > source_published_at)))), CHECK((((publication_precision = 'unknown') AND (source_published_at IS NULL)) OR ((publication_precision != 'unknown') AND (source_published_at IS NOT NULL)))));
CREATE TABLE meta.dataset_release_artifact(release_id BIGINT, artifact_id BIGINT, "role" VARCHAR, FOREIGN KEY (release_id) REFERENCES meta.dataset_release(release_id), FOREIGN KEY (artifact_id) REFERENCES meta.artifact(artifact_id), CHECK(("role" IN ('data', 'publication', 'timing'))), PRIMARY KEY(release_id, artifact_id, "role"));
CREATE MACRO fundamental.fact_asof (as_of_time) AS TABLE (SELECT * EXCLUDE (fact_rank) FROM (SELECT f.*, row_number() OVER (PARTITION BY instrument_id, canonical_field, report_period ORDER BY announcement_time DESC, fact_id DESC) AS fact_rank FROM fundamental.fact AS f WHERE (announcement_time <= as_of_time)) WHERE (fact_rank = 1));
CREATE MACRO fundamental.provider_conflicts_asof (as_of_time) AS TABLE (SELECT * EXCLUDE (observation_rank) FROM (SELECT r.*, a.sha256 AS artifact_sha256, a.fetched_at AS observed_at, row_number() OVER (PARTITION BY r."source", r.provider_code, r.report_period ORDER BY a.fetched_at DESC, r.artifact_id DESC) AS observation_rank FROM fundamental.provider_record_resolution AS r INNER JOIN meta.artifact AS a USING (artifact_id) WHERE (a.fetched_at <= CAST(as_of_time AS "TIMESTAMP WITH TIME ZONE"))) WHERE ((observation_rank = 1) AND starts_with(reason, 'conflicting duplicate provider records:')));
CREATE MACRO fundamental.ttm_asof (as_of_time, end_period, min_instrument_id := NULL, max_instrument_id := NULL) AS TABLE (WITH facts AS (SELECT * FROM fundamental.fact_asof(CAST(as_of_time AS "TIMESTAMP WITH TIME ZONE")) WHERE (((min_instrument_id IS NULL) OR (instrument_id >= min_instrument_id)) AND ((max_instrument_id IS NULL) OR (instrument_id <= max_instrument_id)) AND (report_period <= CAST(end_period AS "DATE")) AND (materializer_version != 'legacy'))), instruments AS (SELECT DISTINCT instrument_id, primary_source, provider_code, statement_scope FROM facts), series AS (SELECT i.*, m.canonical_field, m.unit, CASE  WHEN ((m.value_kind = 'monetary')) THEN ('CNY') ELSE NULL END AS currency, m.period_basis AS basis FROM instruments AS i CROSS JOIN fundamental.field AS m WHERE m.value_kind IN ('monetary','shares') AND m.period_basis IN ('instant','quarter','ytd') AND ((CAST(end_period AS "DATE") = last_day(CAST(end_period AS "DATE"))) AND ("month"(CAST(end_period AS "DATE")) IN (3, 6, 9, 12)))), requirements AS ((SELECT s.*, 0 AS ordinal, CAST(end_period AS "DATE") AS required_period, 1 AS coefficient FROM series AS s WHERE (basis IS NOT NULL)) UNION ALL (SELECT s.*, CAST(n AS INTEGER), last_day((CAST(end_period AS "DATE") - (n * CAST('3 months' AS INTERVAL)))), 1 FROM series AS s CROSS JOIN "range"(1, 4) AS r(n) WHERE (basis = 'quarter'))UNION ALL (SELECT s.*, 1, make_date(("year"(CAST(end_period AS "DATE")) - 1), 12, 31), 1 FROM series AS s WHERE ((basis = 'ytd') AND ("month"(CAST(end_period AS "DATE")) != 12)))UNION ALL (SELECT s.*, 2, last_day((CAST(end_period AS "DATE") - CAST('1 year' AS INTERVAL))), -1 FROM series AS s WHERE ((basis = 'ytd') AND ("month"(CAST(end_period AS "DATE")) != 12)))), inputs AS (SELECT r.*, f.fact_id, f.source_filing_id, f.announcement_time, f."value", f.source_provider_field FROM requirements AS r LEFT JOIN facts AS f ON (((f.instrument_id = r.instrument_id) AND (f.primary_source = r.primary_source) AND (f.provider_code = r.provider_code) AND (f.statement_scope = r.statement_scope) AND (f.canonical_field = r.canonical_field) AND (f.unit = r.unit) AND (f.currency IS NOT DISTINCT FROM r.currency) AND (f.report_period = r.required_period) AND (f.period_type = CASE  WHEN ((r.basis = 'instant')) THEN ('instant') WHEN ((r.basis = 'quarter')) THEN (('Q' || CAST("quarter"(r.required_period) AS VARCHAR))) WHEN (("month"(r.required_period) = 3)) THEN ('Q1') WHEN (("month"(r.required_period) = 6)) THEN ('H1') WHEN ((("month"(r.required_period) = 9) AND (r.basis = 'ytd'))) THEN ('9M') WHEN (("month"(r.required_period) = 9)) THEN ('Q3') ELSE 'FY' END))))SELECT instrument_id, primary_source, provider_code, statement_scope, canonical_field, min(source_provider_field) AS source_provider_field, unit, currency, min(CAST(end_period AS "DATE")) AS report_period, CASE  WHEN ((basis = 'instant')) THEN ('instant') ELSE 'TTM' END AS period_type, basis AS calculation_basis, CASE  WHEN ((count(fact_id) = count_star())) THEN (sum(("value" * coefficient))) ELSE NULL END AS "value", CASE  WHEN ((count(fact_id) = count_star())) THEN ('complete') ELSE 'missing_inputs' END AS coverage_status, count_star() AS required_inputs, count(fact_id) AS available_inputs, max(announcement_time) AS latest_input_announcement_time, list(required_period ORDER BY ordinal) AS input_periods, list(coefficient ORDER BY ordinal) AS input_coefficients, list(fact_id ORDER BY ordinal) AS source_fact_ids, list(source_filing_id ORDER BY ordinal) AS source_filing_ids, list(required_period ORDER BY ordinal) FILTER (WHERE (fact_id IS NULL)) AS missing_periods FROM inputs GROUP BY instrument_id, primary_source, provider_code, statement_scope, canonical_field, unit, currency, basis);
CREATE MACRO fundamental.annual_asof (as_of_time, report_year) AS TABLE (SELECT * REPLACE (CASE  WHEN ((period_type = 'instant')) THEN ('instant') ELSE 'FY' END AS period_type) FROM fundamental.ttm_asof(as_of_time, make_date(CAST(report_year AS INTEGER), 12, 31)));
CREATE VIEW fundamental.fact_latest AS SELECT * EXCLUDE (fact_rank) FROM (SELECT f.*, row_number() OVER (PARTITION BY instrument_id, canonical_field, report_period ORDER BY announcement_time DESC, fact_id DESC) AS fact_rank FROM fundamental.fact AS f) WHERE (fact_rank = 1);
CREATE VIEW reference.risk_observation AS (SELECT * FROM reference.country_risk) UNION ALL (SELECT * FROM reference.equity_risk_premium);
CREATE INDEX daily_observation_lookup ON market.daily_observation(instrument_id, trade_date, "source");


INSERT INTO fundamental.field ("canonical_field","unit","value_kind","period_basis") VALUES
('accounts_payable','CNY','monetary','instant'),
('accounts_receivable','CNY','monetary','instant'),
('adjusted_net_income','CNY','monetary','quarter'),
('asset_disposal_income','CNY','monetary','ytd'),
('bonds_payable','CNY','monetary','instant'),
('capital_expenditure_cash','CNY','monetary','ytd'),
('cash_and_cash_equivalents','CNY','monetary','instant'),
('construction_in_progress','CNY','monetary','instant'),
('contract_liabilities','CNY','monetary','instant'),
('credit_impairment_income','CNY','monetary','ytd'),
('current_assets','CNY','monetary','instant'),
('current_liabilities','CNY','monetary','instant'),
('current_portion_noncurrent_liabilities','CNY','monetary','instant'),
('debt_investments','CNY','monetary','instant'),
('deferred_expense_amortization','CNY','monetary','ytd'),
('deferred_tax_assets','CNY','monetary','instant'),
('deferred_tax_liabilities','CNY','monetary','instant'),
('deposits_and_interbank_placements','CNY','monetary','instant'),
('depreciation_depletion','CNY','monetary','ytd'),
('equity_parent','CNY','monetary','instant'),
('fair_value_change_income','CNY','monetary','ytd'),
('finance_costs','CNY','monetary','ytd'),
('financial_assets_purchased_under_resale_agreements','CNY','monetary','instant'),
('financial_business_fee_expense','CNY','monetary','ytd'),
('financial_business_interest_expense','CNY','monetary','ytd'),
('financial_business_interest_income','CNY','monetary','ytd'),
('financing_cash_flow','CNY','monetary','quarter'),
('fixed_assets_net','CNY','monetary','instant'),
('funds_lent','CNY','monetary','instant'),
('income_tax_expense','CNY','monetary','ytd'),
('intangible_amortization','CNY','monetary','ytd'),
('intangible_assets_net','CNY','monetary','instant'),
('interest_expense','CNY','monetary','ytd'),
('interest_income','CNY','monetary','ytd'),
('inventories','CNY','monetary','instant'),
('inventory_decrease_cashflow','CNY','monetary','ytd'),
('investing_cash_flow','CNY','monetary','quarter'),
('investment_income','CNY','monetary','ytd'),
('investment_property_depreciation_amortization','CNY','monetary','ytd'),
('lease_liabilities','CNY','monetary','instant'),
('loans_and_advances_noncurrent','CNY','monetary','instant'),
('long_lived_asset_disposal_cash','CNY','monetary','ytd'),
('long_term_borrowings','CNY','monetary','instant'),
('long_term_equity_investments','CNY','monetary','instant'),
('monetary_funds','CNY','monetary','instant'),
('net_cash_increase','CNY','monetary','quarter'),
('net_income_minority_ytd','CNY','monetary','ytd'),
('net_income_parent','CNY','monetary','quarter'),
('net_income_parent_ytd','CNY','monetary','ytd'),
('net_income_ytd','CNY','monetary','ytd'),
('noncontrolling_interests','CNY','monetary','instant'),
('noncurrent_assets_due_within_one_year','CNY','monetary','instant'),
('operating_cash_flow','CNY','monetary','quarter'),
('operating_payables_increase_cashflow','CNY','monetary','ytd'),
('operating_profit','CNY','monetary','quarter'),
('operating_profit_cumulative','CNY','monetary','ytd'),
('operating_receivables_decrease_cashflow','CNY','monetary','ytd'),
('other_current_assets','CNY','monetary','instant'),
('other_current_liabilities','CNY','monetary','instant'),
('other_debt_investments','CNY','monetary','instant'),
('other_equity_instruments','CNY','monetary','instant'),
('other_noncurrent_financial_assets','CNY','monetary','instant'),
('other_payables','CNY','monetary','instant'),
('other_receivables','CNY','monetary','instant'),
('payroll_payable','CNY','monetary','instant'),
('prepayments','CNY','monetary','instant'),
('profit_before_tax','CNY','monetary','ytd'),
('provisions','CNY','monetary','instant'),
('receivables_financing','CNY','monetary','instant'),
('research_and_development_expense','CNY','monetary','ytd'),
('revenue','CNY','monetary','quarter'),
('right_of_use_depreciation','CNY','monetary','ytd'),
('short_term_borrowings','CNY','monetary','instant'),
('tax_refunds_received','CNY','monetary','ytd'),
('taxes_paid','CNY','monetary','ytd'),
('taxes_payable','CNY','monetary','instant'),
('total_assets','CNY','monetary','instant'),
('total_equity','CNY','monetary','instant'),
('total_liabilities','CNY','monetary','instant'),
('total_shares','share','shares','instant'),
('trading_financial_assets','CNY','monetary','instant');

INSERT INTO fundamental.provider_field ("source","provider_field","canonical_field","display_name","unit","value_kind","valid_from","valid_to","notes","period_basis","value_multiplier") VALUES
('tdx','FN104','taxes_paid','支付的各项税费','CNY','monetary','2025-01-01',NULL,'Cashflow statement; not operating income tax paid; cash-rd-2026','ytd','1'),
('tdx','FN11','accounts_receivable','应收账款','CNY','monetary','2025-01-01',NULL,'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3','instant','1'),
('tdx','FN110','long_lived_asset_disposal_cash','处置固定资产、无形资产和其他长期资产收回的现金净额','CNY','monetary','2025-01-01',NULL,'TDX official FN110; Anker 2025H1/FY and 2026H1 consolidated cashflow source bits checked; cash proceeds, not disposal gain, subsidiary disposal or financial investment recovery; zero ambiguity retained','ytd','1'),
('tdx','FN114','capital_expenditure_cash','购建固定资产、无形资产和其他长期资产支付的现金','CNY','monetary','2024-06-30',NULL,'TDX official FN catalogue; cumulative raw yuan verified against consolidated CNINFO statements: Anker 2024 H1/Q3/FY and existing 2025 H1/Q3 samples','ytd','1'),
('tdx','FN12','prepayments','预付款项','CNY','monetary','2025-01-01',NULL,'Verified consolidated statement amounts; earnings-working-capital-2026','instant','1'),
('tdx','FN13','other_receivables','其他应收款','CNY','monetary','2024-12-31',NULL,'Net carrying amount, raw yuan, instant; Anker 2024 FY original p155 verified in company-inputs-20260917/anker-opening-2024; prior audited scope retained','instant','1'),
('tdx','FN133','cash_and_cash_equivalents','期末现金及现金等价物余额','CNY','monetary','2025-01-01',NULL,'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3','instant','1'),
('tdx','FN136','depreciation_depletion','固定资产折旧、油气资产折耗、生产性生物资产折旧','CNY','monetary','2025-01-01',NULL,'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3','ytd','1'),
('tdx','FN137','intangible_amortization','无形资产摊销','CNY','monetary','2025-01-01',NULL,'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3','ytd','1'),
('tdx','FN138','deferred_expense_amortization','长期待摊费用摊销','CNY','monetary','2025-01-01',NULL,'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3','ytd','1'),
('tdx','FN146','inventory_decrease_cashflow','存货的减少','CNY','monetary','2025-01-01',NULL,'Cashflow reconciliation, decrease positive/increase negative; cash-rd-2026','ytd','1'),
('tdx','FN147','operating_receivables_decrease_cashflow','经营性应收项目的减少','CNY','monetary','2025-01-01',NULL,'Cashflow reconciliation; not balance sheet receivables delta; cash-rd-2026','ytd','1'),
('tdx','FN148','operating_payables_increase_cashflow','经营性应付项目的增加','CNY','monetary','2025-01-01',NULL,'Cashflow reconciliation; not classified valuation working capital; cash-rd-2026','ytd','1'),
('tdx','FN17','inventories','存货','CNY','monetary','2025-01-01',NULL,'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3','instant','1'),
('tdx','FN19','noncurrent_assets_due_within_one_year','一年内到期的非流动资产','CNY','monetary','2025-01-01',NULL,'Total current portion of noncurrent assets; financial/nonfinancial classification is separate; balance-profit-2026','instant','1'),
('tdx','FN20','other_current_assets','其他流动资产','CNY','monetary','2025-01-01',NULL,'Mixed balance; not entirely operating working capital; balance-profit-2026','instant','1'),
('tdx','FN21','current_assets','流动资产合计','CNY','monetary','2025-01-01',NULL,'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3','instant','1'),
('tdx','FN230','revenue','营业收入','CNY','monetary','1900-01-01',NULL,'TDX official professional-financial field','quarter','1'),
('tdx','FN231','operating_profit','营业利润','CNY','monetary','1900-01-01',NULL,'TDX official professional-financial field','quarter','1'),
('tdx','FN232','net_income_parent','归属于母公司所有者的净利润','CNY','monetary','1900-01-01',NULL,'TDX official professional-financial field','quarter','1'),
('tdx','FN233','adjusted_net_income','扣除非经常性损益后的净利润','CNY','monetary','1900-01-01',NULL,'TDX official professional-financial field','quarter','1'),
('tdx','FN234','operating_cash_flow','经营活动产生的现金流量净额','CNY','monetary','1900-01-01',NULL,'TDX official professional-financial field','quarter','1'),
('tdx','FN235','investing_cash_flow','投资活动产生的现金流量净额','CNY','monetary','1900-01-01',NULL,'TDX official professional-financial field','quarter','1'),
('tdx','FN236','financing_cash_flow','筹资活动产生的现金流量净额','CNY','monetary','1900-01-01',NULL,'TDX official professional-financial field','quarter','1'),
('tdx','FN237','net_cash_increase','现金及现金等价物净增加额','CNY','monetary','1900-01-01',NULL,'TDX official professional-financial field','quarter','1'),
('tdx','FN238','total_shares','总股本','share','shares','1900-01-01',NULL,'TDX official professional-financial field','instant','1'),
('tdx','FN25','long_term_equity_investments','长期股权投资','CNY','monetary','2025-01-01',NULL,'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3','instant','1'),
('tdx','FN27','fixed_assets_net','固定资产','CNY','monetary','2025-01-01',NULL,'Net carrying amount, not gross original cost or cash capital expenditure; balance-profit-2026','instant','1'),
('tdx','FN271','equity_parent','归属于母公司股东权益','CNY','monetary','2025-01-01',NULL,'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3','instant','1'),
('tdx','FN28','construction_in_progress','在建工程','CNY','monetary','2025-01-01',NULL,'Balance, not cash capital expenditure; balance-profit-2026','instant','1'),
('tdx','FN299','other_equity_instruments','其他权益工具','CNY','monetary','2025-01-01',NULL,'All other equity instruments; sample convertible equity component is not market value; financial-instruments-2026','instant','1'),
('tdx','FN301','asset_disposal_income','资产处置收益','CNY','monetary','2024-06-30',NULL,'FN301: cumulative raw yuan; original consolidated Anker 2024 H1/9M/FY verified in anker-cash-history-2024/earnings-verified.json; prior audited scope retained; interest-note composition confirmed for H1/FY only','ytd','1'),
('tdx','FN304','research_and_development_expense','研发费用','CNY','monetary','2025-01-01',NULL,'Income statement expense; not total R&D spending or capitalized development; cash-rd-2026','ytd','1'),
('tdx','FN305','interest_expense','利息费用','CNY','monetary','2024-06-30',NULL,'FN305: cumulative raw yuan; original consolidated Anker 2024 H1/9M/FY verified in anker-cash-history-2024/earnings-verified.json; prior audited scope retained; interest-note composition confirmed for H1/FY only','ytd','1'),
('tdx','FN306','interest_income','利息收入','CNY','monetary','2024-06-30',NULL,'FN306: cumulative raw yuan; original consolidated Anker 2024 H1/9M/FY verified in anker-cash-history-2024/earnings-verified.json; prior audited scope retained; interest-note composition confirmed for H1/FY only','ytd','1'),
('tdx','FN33','intangible_assets_net','无形资产','CNY','monetary','2025-01-01',NULL,'Net carrying amount, not capitalized research history; balance-profit-2026','instant','1'),
('tdx','FN37','deferred_tax_assets','递延所得税资产','CNY','monetary','2025-01-01',NULL,'Balance, not cash tax payment or cashflow reconciliation decrease; balance-profit-2026','instant','1'),
('tdx','FN40','total_assets','资产总计','CNY','monetary','2025-01-01',NULL,'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3','instant','1'),
('tdx','FN403','funds_lent','拆出资金','CNY','monetary','2025-01-01',NULL,'Consolidated interbank business assets; not unrestricted operating cash; financial-instruments-2026','instant','10000'),
('tdx','FN409','financial_assets_purchased_under_resale_agreements','买入返售金融资产','CNY','monetary','2025-01-01',NULL,'Financial assets under resale agreements; not operating cash; financial-instruments-2026','instant','10000'),
('tdx','FN41','short_term_borrowings','短期借款','CNY','monetary','2025-01-01',NULL,'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3','instant','1'),
('tdx','FN411','loans_and_advances_noncurrent','发放贷款及垫款（非流动）','CNY','monetary','2025-01-01',NULL,'Noncurrent loans and advances; not related-party receivable note components; financial-instruments-2026','instant','10000'),
('tdx','FN413','deposits_and_interbank_placements','吸收存款及同业存放','CNY','monetary','2025-01-01',NULL,'External deposits at consolidated financial subsidiary; not issuer interest-bearing borrowing; financial-instruments-2026','instant','10000'),
('tdx','FN430','debt_investments','债权投资','CNY','monetary','2025-01-01',NULL,'Carrying amount, not market value or debt asset maturity split; financial-instruments-2026','instant','10000'),
('tdx','FN431','other_debt_investments','其他债权投资','CNY','monetary','2025-01-01',NULL,'Carrying amount, not market value; financial-instruments-2026','instant','10000'),
('tdx','FN433','other_noncurrent_financial_assets','其他非流动金融资产','CNY','monetary','2025-01-01',NULL,'Total other noncurrent financial assets; not equity-only funds universally; financial-instruments-2026','instant','10000'),
('tdx','FN434','contract_liabilities','合同负债','CNY','monetary','2025-01-01',NULL,'Contract liabilities; distinct from taxes or advances received; financial-instruments-2026','instant','10000'),
('tdx','FN437','receivables_financing','应收款项融资','CNY','monetary','2025-01-01',NULL,'Receivables financing; not notes receivable FN10 or receivables FN11; financial-instruments-2026','instant','10000'),
('tdx','FN439','lease_liabilities','租赁负债','CNY','monetary','2025-01-01',NULL,'Verified against consolidated CNINFO 2025 reports; see tax-debt-2025 samples','instant','10000'),
('tdx','FN44','accounts_payable','应付账款','CNY','monetary','2025-01-01',NULL,'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3','instant','1'),
('tdx','FN46','payroll_payable','应付职工薪酬','CNY','monetary','2025-01-01',NULL,'Verified consolidated statement amounts; earnings-working-capital-2026','instant','1'),
('tdx','FN47','taxes_payable','应交税费','CNY','monetary','2025-01-01',NULL,'All taxes payable, not income tax payable; earnings-working-capital-2026','instant','1'),
('tdx','FN50','other_payables','其他应付款','CNY','monetary','2025-01-01',NULL,'Includes mixed operating/nonoperating items; not all valuation working capital; balance-profit-2026','instant','1'),
('tdx','FN506','financial_business_interest_income','金融业务利息收入','CNY','monetary','2025-01-01',NULL,'Income statement financial-business interest income; not treasury interest FN306; financial-instruments-2026','ytd','10000'),
('tdx','FN509','financial_business_interest_expense','金融业务利息支出','CNY','monetary','2025-01-01',NULL,'Income statement financial-business interest expense; not finance-cost interest FN305; financial-instruments-2026','ytd','10000'),
('tdx','FN510','financial_business_fee_expense','手续费及佣金支出','CNY','monetary','2025-01-01',NULL,'Income statement financial-business fee expense; financial-instruments-2026','ytd','10000'),
('tdx','FN52','current_portion_noncurrent_liabilities','一年内到期的非流动负债','CNY','monetary','2025-01-01',NULL,'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3','instant','1'),
('tdx','FN520','credit_impairment_income','信用减值损益（收益正、损失负）','CNY','monetary','2025-01-01',NULL,'2019 income statement format; gain positive/loss negative, not CF allowance FN580; financial-instruments-2026','ytd','10000'),
('tdx','FN53','other_current_liabilities','其他流动负债','CNY','monetary','2025-01-01',NULL,'Mixed liabilities; not all valuation working capital; balance-profit-2026','instant','1'),
('tdx','FN54','current_liabilities','流动负债合计','CNY','monetary','2025-01-01',NULL,'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3','instant','1'),
('tdx','FN55','long_term_borrowings','长期借款','CNY','monetary','2025-01-01',NULL,'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3','instant','1'),
('tdx','FN56','bonds_payable','应付债券','CNY','monetary','2025-01-01',NULL,'Verified against consolidated CNINFO 2025 reports; see tax-debt-2025 samples','instant','1'),
('tdx','FN579','investment_property_depreciation_amortization','投资性房地产折旧及摊销','CNY','monetary','2025-01-01',NULL,'Cashflow reconciliation investment property depreciation; not FN136 or FN581; financial-instruments-2026','ytd','10000'),
('tdx','FN581','right_of_use_depreciation','使用权资产折旧','CNY','monetary','2025-01-01',NULL,'Verified against consolidated CNINFO 2025 reports; see tax-debt-2025 samples','ytd','10000'),
('tdx','FN59','provisions','预计负债','CNY','monetary','2025-01-01',NULL,'Total provisions; sample lease/nonoperating classification is not universal; financial-instruments-2026','instant','1'),
('tdx','FN60','deferred_tax_liabilities','递延所得税负债','CNY','monetary','2025-01-01',NULL,'Balance, not cash tax payment or cashflow reconciliation increase; balance-profit-2026','instant','1'),
('tdx','FN63','total_liabilities','负债合计','CNY','monetary','2025-01-01',NULL,'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3','instant','1'),
('tdx','FN69','noncontrolling_interests','少数股东权益','CNY','monetary','2025-01-01',NULL,'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3','instant','1'),
('tdx','FN72','total_equity','所有者权益合计','CNY','monetary','2025-01-01',NULL,'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3','instant','1'),
('tdx','FN8','monetary_funds','货币资金','CNY','monetary','2025-01-01',NULL,'TDX official FN catalogue; raw yuan verified against consolidated CNINFO statements, 2025 H1/Q3','instant','1'),
('tdx','FN80','finance_costs','财务费用','CNY','monetary','2025-01-01',NULL,'Verified against consolidated CNINFO 2025 reports; see tax-debt-2025 samples','ytd','1'),
('tdx','FN82','fair_value_change_income','公允价值变动收益','CNY','monetary','2024-06-30',NULL,'FN82: cumulative raw yuan; original consolidated Anker 2024 H1/9M/FY verified in anker-cash-history-2024/earnings-verified.json; prior audited scope retained; interest-note composition confirmed for H1/FY only','ytd','1'),
('tdx','FN83','investment_income','投资收益','CNY','monetary','2024-06-30',NULL,'FN83: cumulative raw yuan; original consolidated Anker 2024 H1/9M/FY verified in anker-cash-history-2024/earnings-verified.json; prior audited scope retained; interest-note composition confirmed for H1/FY only','ytd','1'),
('tdx','FN86','operating_profit_cumulative','累计营业利润','CNY','monetary','2024-06-30',NULL,'FN86: cumulative raw yuan; original consolidated Anker 2024 H1/9M/FY verified in anker-cash-history-2024/earnings-verified.json; prior audited scope retained; interest-note composition confirmed for H1/FY only','ytd','1'),
('tdx','FN9','trading_financial_assets','交易性金融资产','CNY','monetary','2025-01-01',NULL,'Total trading financial assets; debt/equity/derivative split remains a note supplement; financial-instruments-2026','instant','1'),
('tdx','FN92','profit_before_tax','利润总额','CNY','monetary','2025-01-01',NULL,'Verified against consolidated CNINFO 2025 reports; see tax-debt-2025 samples','ytd','1'),
('tdx','FN93','income_tax_expense','所得税费用','CNY','monetary','2025-01-01',NULL,'Verified against consolidated CNINFO 2025 reports; see tax-debt-2025 samples','ytd','1'),
('tdx','FN95','net_income_ytd','净利润（累计）','CNY','monetary','2025-01-01',NULL,'Consolidated income statement YTD; not parent-only FN232 single quarter or FN134 cashflow adjustment; balance-profit-2026','ytd','1'),
('tdx','FN96','net_income_parent_ytd','归属于母公司所有者的净利润（累计）','CNY','monetary','2025-01-01',NULL,'Parent income YTD; distinct from FN232 single quarter; balance-profit-2026','ytd','1'),
('tdx','FN97','net_income_minority_ytd','少数股东损益（累计）','CNY','monetary','2025-01-01',NULL,'Minority profit/loss YTD; not minority book equity or segment allocation; balance-profit-2026','ytd','1'),
('tdx','FN99','tax_refunds_received','收到的税费返还','CNY','monetary','2025-01-01',NULL,'Cashflow statement; not income tax benefit; cash-rd-2026','ytd','1');
INSERT INTO meta.schema_version(version,description) VALUES (50,'Current schema baseline');

CREATE TABLE fundamental.source_field (
 source VARCHAR NOT NULL, provider_field VARCHAR NOT NULL, source_index INTEGER NOT NULL,
 name VARCHAR, display_name VARCHAR NOT NULL, category VARCHAR NOT NULL,
 value_kind VARCHAR NOT NULL, unit VARCHAR NOT NULL, value_multiplier DOUBLE,
 period_basis VARCHAR NOT NULL, definition_status VARCHAR NOT NULL,
 definition_reference VARCHAR, catalog_version VARCHAR NOT NULL, statement VARCHAR NOT NULL, section VARCHAR NOT NULL, mapping_status VARCHAR NOT NULL, review_reason VARCHAR NOT NULL,
 PRIMARY KEY(source,provider_field), UNIQUE(source,source_index),
 CHECK(source_index>0), CHECK(value_multiplier IS NULL OR value_multiplier IN (1,10000)),
 CHECK(definition_status IN ('official','reference','unpublished')),
 CHECK(statement IN ('','balance_sheet','income_statement','cash_flow_statement')),
 CHECK(section IN ('','main','supplement')),
 CHECK((statement='' AND section='') OR (statement<>'' AND section<>'')),
 CHECK(mapping_status IN ('not_reviewed','reviewed_mapping','official_mapping'))
);

UPDATE fundamental.provider_field SET zero_policy='allow' WHERE source='tdx' AND provider_field IN ('FN8','FN11','FN12','FN13','FN17','FN21','FN25','FN40','FN41','FN44','FN46','FN47','FN52','FN54','FN55','FN56','FN63','FN69','FN72','FN80','FN82','FN83','FN86','FN92','FN93','FN114','FN133','FN230','FN231','FN232','FN233','FN234','FN235','FN236','FN237','FN238','FN271','FN301','FN305','FN306','FN439');

-- Original consolidated statements verified by statement-expansion-20260919; zeros remain rejected.
INSERT INTO fundamental.field (canonical_field,unit,value_kind,period_basis) VALUES
('accumulated_other_comprehensive_income','CNY','monetary','instant'),
('administrative_expenses','CNY','monetary','ytd'),
('asset_impairment_income','CNY','monetary','ytd'),
('associates_and_joint_ventures_investment_income','CNY','monetary','ytd'),
('borrowing_proceeds','CNY','monetary','ytd'),
('capital_reserve','CNY','monetary','instant'),
('cash_paid_to_employees','CNY','monetary','ytd'),
('cash_paid_to_suppliers','CNY','monetary','ytd'),
('cash_received_from_customers','CNY','monetary','ytd'),
('comprehensive_income','CNY','monetary','ytd'),
('comprehensive_income_minority','CNY','monetary','ytd'),
('comprehensive_income_parent','CNY','monetary','ytd'),
('continuing_operations_net_income','CNY','monetary','ytd'),
('cost_of_revenue','CNY','monetary','ytd'),
('debt_repayment_cash','CNY','monetary','ytd'),
('deferred_income_noncurrent','CNY','monetary','instant'),
('derivative_financial_assets','CNY','monetary','instant'),
('derivative_financial_liabilities','CNY','monetary','instant'),
('dividends_and_interest_paid','CNY','monetary','ytd'),
('dividends_receivable','CNY','monetary','instant'),
('equity_financing_cash','CNY','monetary','ytd'),
('exchange_rate_effect_on_cash','CNY','monetary','ytd'),
('financing_cash_flow_cumulative','CNY','monetary','ytd'),
('financing_cash_inflows','CNY','monetary','ytd'),
('financing_cash_outflows','CNY','monetary','ytd'),
('investing_cash_flow_cumulative','CNY','monetary','ytd'),
('investing_cash_inflows','CNY','monetary','ytd'),
('investing_cash_outflows','CNY','monetary','ytd'),
('investment_income_cash','CNY','monetary','ytd'),
('investment_property','CNY','monetary','instant'),
('investment_purchase_cash','CNY','monetary','ytd'),
('investment_recovery_cash','CNY','monetary','ytd'),
('liabilities_and_equity','CNY','monetary','instant'),
('long_term_employee_benefits_payable','CNY','monetary','instant'),
('long_term_prepaid_expenses','CNY','monetary','instant'),
('net_cash_increase_cumulative','CNY','monetary','ytd'),
('noncurrent_assets','CNY','monetary','instant'),
('noncurrent_liabilities','CNY','monetary','instant'),
('nonoperating_expenses','CNY','monetary','ytd'),
('nonoperating_income','CNY','monetary','ytd'),
('notes_payable','CNY','monetary','instant'),
('operating_cash_flow_cumulative','CNY','monetary','ytd'),
('operating_cash_inflows','CNY','monetary','ytd'),
('operating_cash_outflows','CNY','monetary','ytd'),
('other_comprehensive_income_profit_statement','CNY','monetary','ytd'),
('other_financing_cash_paid','CNY','monetary','ytd'),
('other_income','CNY','monetary','ytd'),
('other_investing_cash_paid','CNY','monetary','ytd'),
('other_investing_cash_received','CNY','monetary','ytd'),
('other_noncurrent_assets','CNY','monetary','instant'),
('other_operating_cash_paid','CNY','monetary','ytd'),
('other_operating_cash_received','CNY','monetary','ytd'),
('paid_in_capital','CNY','monetary','instant'),
('retained_earnings','CNY','monetary','instant'),
('revenue_cumulative','CNY','monetary','ytd'),
('right_of_use_assets','CNY','monetary','instant'),
('selling_expenses','CNY','monetary','ytd'),
('subsidiary_minority_dividends_cash_paid','CNY','monetary','ytd'),
('subsidiary_minority_equity_cash_received','CNY','monetary','ytd'),
('surplus_reserve','CNY','monetary','instant'),
('taxes_and_surcharges','CNY','monetary','ytd'),
('total_operating_cost','CNY','monetary','ytd'),
('total_operating_revenue','CNY','monetary','ytd'),
('trading_financial_liabilities','CNY','monetary','instant');
INSERT INTO fundamental.provider_field (source,provider_field,canonical_field,display_name,unit,value_kind,valid_from,notes,period_basis,value_multiplier,zero_policy) VALUES
('tdx','FN298','accumulated_other_comprehensive_income','其他综合收益(资产负债表)','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','instant','1','reject'),
('tdx','FN78','administrative_expenses','管理费用','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN521','asset_impairment_income','资产减值损失(万元、2019格式)','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','10000','reject'),
('tdx','FN84','associates_and_joint_ventures_investment_income','其中：对联营企业和合营企业的投资收益','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN121','borrowing_proceeds','取得借款收到的现金','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN65','capital_reserve','资本公积','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','instant','1','reject'),
('tdx','FN103','cash_paid_to_employees','支付给职工以及为职工支付的现金','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN102','cash_paid_to_suppliers','购买商品、接受劳务支付的现金','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN98','cash_received_from_customers','销售商品、提供劳务收到的现金','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN270','comprehensive_income','综合收益总额(利润表)','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN505','comprehensive_income_minority','其中:归属于少数股东综合收益(万元)','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','10000','reject'),
('tdx','FN504','comprehensive_income_parent','其中:归属于母公司综合收益(万元)','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','10000','reject'),
('tdx','FN302','continuing_operations_net_income','持续经营净利润(利润表)','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN75','cost_of_revenue','其中：营业成本','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN124','debt_repayment_cash','偿还债务支付的现金','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN297','deferred_income_noncurrent','递延收益(资产负债表-非流动负债)','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','instant','1','reject'),
('tdx','FN405','derivative_financial_assets','衍生金融资产(万元)','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','instant','10000','reject'),
('tdx','FN415','derivative_financial_liabilities','衍生金融负债(万元)','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','instant','10000','reject'),
('tdx','FN125','dividends_and_interest_paid','分配股利、利润或偿付利息支付的现金','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN16','dividends_receivable','应收股利','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','instant','1','reject'),
('tdx','FN120','equity_financing_cash','吸收投资收到的现金','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN129','exchange_rate_effect_on_cash','四、汇率变动对现金的影响','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN128','financing_cash_flow_cumulative','筹资活动产生的现金流量净额','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN123','financing_cash_inflows','筹资活动现金流入小计','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN127','financing_cash_outflows','筹资活动现金流出小计','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN119','investing_cash_flow_cumulative','投资活动产生的现金流量净额','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN113','investing_cash_inflows','投资活动现金流入小计','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN118','investing_cash_outflows','投资活动现金流出小计','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN109','investment_income_cash','取得投资收益收到的现金','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN26','investment_property','投资性房地产','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','instant','1','reject'),
('tdx','FN115','investment_purchase_cash','投资支付的现金','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN108','investment_recovery_cash','收回投资收到的现金','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN73','liabilities_and_equity','负债和所有者（或股东权益）合计','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','instant','1','reject'),
('tdx','FN427','long_term_employee_benefits_payable','长期应付职工薪酬(万元)','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','instant','10000','reject'),
('tdx','FN36','long_term_prepaid_expenses','长期待摊费用','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','instant','1','reject'),
('tdx','FN131','net_cash_increase_cumulative','五、现金及现金等价物净增加额','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN39','noncurrent_assets','非流动资产合计','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','instant','1','reject'),
('tdx','FN62','noncurrent_liabilities','非流动负债合计','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','instant','1','reject'),
('tdx','FN89','nonoperating_expenses','减：营业外支出','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN88','nonoperating_income','营业外收入','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN43','notes_payable','应付票据','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','instant','1','reject'),
('tdx','FN107','operating_cash_flow_cumulative','经营活动产生的现金流量净额','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN101','operating_cash_inflows','经营活动现金流入小计','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN106','operating_cash_outflows','经营活动现金流出小计','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN269','other_comprehensive_income_profit_statement','其他综合收益(利润表)','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN126','other_financing_cash_paid','支付其他与筹资活动有关的现金','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN300','other_income','其他收益(利润表)','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN117','other_investing_cash_paid','支付其他与投资活动有关的现金','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN112','other_investing_cash_received','收到其他与投资活动有关的现金','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN38','other_noncurrent_assets','其他非流动资产','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','instant','1','reject'),
('tdx','FN105','other_operating_cash_paid','支付其他与经营活动有关的现金','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN100','other_operating_cash_received','收到其他与经营活动有关的现金','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN64','paid_in_capital','实收资本（或股本）','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','instant','1','reject'),
('tdx','FN68','retained_earnings','未分配利润','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','instant','1','reject'),
('tdx','FN74','revenue_cumulative','其中：营业收入','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN438','right_of_use_assets','使用权资产(万元)','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','instant','10000','reject'),
('tdx','FN77','selling_expenses','销售费用','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN578','subsidiary_minority_dividends_cash_paid','其中:子公司支付给少数股东的股利、利润(万元)','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','10000','reject'),
('tdx','FN577','subsidiary_minority_equity_cash_received','其中:子公司吸收少数股东投资收到的现金(万元)','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','10000','reject'),
('tdx','FN66','surplus_reserve','盈余公积','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','instant','1','reject'),
('tdx','FN76','taxes_and_surcharges','营业税金及附加','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','1','reject'),
('tdx','FN519','total_operating_cost','营业总成本(万元)','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','10000','reject'),
('tdx','FN502','total_operating_revenue','营业总收入(万元)','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','ytd','10000','reject'),
('tdx','FN42','trading_financial_liabilities','交易性金融负债','CNY','monetary','2025-01-01','statement-expansion-20260919; original consolidated current column; nonzero only','instant','1','reject');

-- Cashflow reconciliation observations remain distinct from income statement items.
INSERT INTO fundamental.field VALUES
('cashflow_reconciliation_net_income','CNY','monetary','ytd'),
('cashflow_asset_impairment_provisions','CNY','monetary','ytd'),
('cashflow_long_lived_asset_disposal_loss','CNY','monetary','ytd'),
('fixed_asset_retirement_loss','CNY','monetary','ytd'),
('cashflow_fair_value_loss','CNY','monetary','ytd'),
('cashflow_finance_costs','CNY','monetary','ytd'),
('cashflow_investment_loss','CNY','monetary','ytd'),
('deferred_tax_asset_decrease_cashflow','CNY','monetary','ytd'),
('deferred_tax_liability_increase_cashflow','CNY','monetary','ytd'),
('operating_cash_flow_indirect','CNY','monetary','ytd'),
('net_cash_increase_reconciliation','CNY','monetary','ytd'),
('credit_impairment_cashflow_adjustment','CNY','monetary','ytd');
INSERT INTO fundamental.provider_field (source,provider_field,canonical_field,display_name,unit,value_kind,valid_from,notes,period_basis,value_multiplier,zero_policy) VALUES
('tdx','FN134','cashflow_reconciliation_net_income','净利润','CNY','monetary','2025-01-01','cashflow-reconciliation-20260919; original consolidated supplementary current column; nonzero only','ytd',1,'reject'),
('tdx','FN135','cashflow_asset_impairment_provisions','加：资产减值准备','CNY','monetary','2025-01-01','cashflow-reconciliation-20260919; original consolidated supplementary current column; nonzero only','ytd',1,'reject'),
('tdx','FN139','cashflow_long_lived_asset_disposal_loss','处置固定资产、无形资产和其他长期资产的损失','CNY','monetary','2025-01-01','cashflow-reconciliation-20260919; original consolidated supplementary current column; nonzero only','ytd',1,'reject'),
('tdx','FN140','fixed_asset_retirement_loss','固定资产报废损失','CNY','monetary','2025-01-01','cashflow-reconciliation-20260919; original consolidated supplementary current column; nonzero only','ytd',1,'reject'),
('tdx','FN141','cashflow_fair_value_loss','公允价值变动损失','CNY','monetary','2025-01-01','cashflow-reconciliation-20260919; original consolidated supplementary current column; nonzero only','ytd',1,'reject'),
('tdx','FN142','cashflow_finance_costs','财务费用','CNY','monetary','2025-01-01','cashflow-reconciliation-20260919; original consolidated supplementary current column; nonzero only','ytd',1,'reject'),
('tdx','FN143','cashflow_investment_loss','投资损失','CNY','monetary','2025-01-01','cashflow-reconciliation-20260919; original consolidated supplementary current column; nonzero only','ytd',1,'reject'),
('tdx','FN144','deferred_tax_asset_decrease_cashflow','递延所得税资产减少','CNY','monetary','2025-01-01','cashflow-reconciliation-20260919; original consolidated supplementary current column; nonzero only','ytd',1,'reject'),
('tdx','FN145','deferred_tax_liability_increase_cashflow','递延所得税负债增加','CNY','monetary','2025-01-01','cashflow-reconciliation-20260919; original consolidated supplementary current column; nonzero only','ytd',1,'reject'),
('tdx','FN150','operating_cash_flow_indirect','经营活动产生的现金流量净额2','CNY','monetary','2025-01-01','cashflow-reconciliation-20260919; original consolidated supplementary current column; nonzero only','ytd',1,'reject'),
('tdx','FN158','net_cash_increase_reconciliation','现金及现金等价物净增加额','CNY','monetary','2025-01-01','cashflow-reconciliation-20260919; original consolidated supplementary current column; nonzero only','ytd',1,'reject'),
('tdx','FN580','credit_impairment_cashflow_adjustment','信用减值损失(万元)','CNY','monetary','2025-01-01','cashflow-reconciliation-20260919; original consolidated supplementary current column; nonzero only','ytd',10000,'reject');

-- Official documented statement definitions; zero remains ambiguous, individual values are not PDF certified.
INSERT INTO fundamental.field VALUES
('basic_earnings_per_share','CNY/share','per_share','ytd'),
('notes_receivable','CNY','monetary','instant'),
('related_party_receivables','CNY','monetary','instant'),
('interest_receivable','CNY','monetary','instant'),
('consumable_biological_assets','CNY','monetary','instant'),
('available_for_sale_financial_assets','CNY','monetary','instant'),
('held_to_maturity_investments','CNY','monetary','instant'),
('long_term_receivables','CNY','monetary','instant'),
('construction_materials','CNY','monetary','instant'),
('fixed_assets_pending_disposal','CNY','monetary','instant'),
('productive_biological_assets','CNY','monetary','instant'),
('oil_and_gas_assets','CNY','monetary','instant'),
('development_costs','CNY','monetary','instant'),
('goodwill','CNY','monetary','instant'),
('advance_receipts','CNY','monetary','instant'),
('interest_payable','CNY','monetary','instant'),
('dividends_payable','CNY','monetary','instant'),
('related_party_payables','CNY','monetary','instant'),
('long_term_payables','CNY','monetary','instant'),
('special_payables','CNY','monetary','instant'),
('other_noncurrent_liabilities','CNY','monetary','instant'),
('treasury_stock','CNY','monetary','instant'),
('foreign_currency_translation_difference','CNY','monetary','instant'),
('exploration_expenses','CNY','monetary','ytd'),
('asset_impairment_loss','CNY','monetary','ytd'),
('other_operating_profit_adjustments','CNY','monetary','ytd'),
('subsidy_income','CNY','monetary','ytd'),
('noncurrent_asset_disposal_loss','CNY','monetary','ytd'),
('other_pretax_profit_adjustments','CNY','monetary','ytd'),
('other_net_income_adjustments','CNY','monetary','ytd'),
('subsidiary_disposal_cash','CNY','monetary','ytd'),
('subsidiary_acquisition_cash','CNY','monetary','ytd'),
('other_financing_cash_received','CNY','monetary','ytd'),
('other_effects_on_cash','CNY','monetary','ytd'),
('opening_cash_and_cash_equivalents','CNY','monetary','opening_instant'),
('other_operating_cashflow_adjustments','CNY','monetary','ytd'),
('debt_converted_to_equity','CNY','monetary','ytd'),
('convertible_bonds_due_within_one_year','CNY','monetary','instant'),
('finance_leased_fixed_assets','CNY','monetary','ytd'),
('closing_cash','CNY','monetary','instant'),
('opening_cash','CNY','monetary','opening_instant'),
('closing_cash_equivalents','CNY','monetary','instant'),
('opening_cash_equivalents','CNY','monetary','opening_instant'),
('general_risk_reserve','CNY','monetary','instant'),
('notes_and_accounts_payable','CNY','monetary','instant'),
('notes_and_accounts_receivable','CNY','monetary','instant'),
('discontinued_operations_net_income','CNY','monetary','ytd'),
('basic_earnings_per_share_quarter','CNY/share','per_share','quarter'),
('total_operating_revenue_quarter','CNY','monetary','quarter'),
('net_income_quarter','CNY','monetary','quarter'),
('cost_of_revenue_quarter','CNY','monetary','quarter'),
('domestic_main_business_revenue','CNY','monetary','ytd'),
('overseas_main_business_revenue','CNY','monetary','ytd'),
('special_reserve','CNY','monetary','instant'),
('settlement_reserve','CNY','monetary','instant'),
('loans_and_advances_current','CNY','monetary','instant'),
('premiums_receivable','CNY','monetary','instant'),
('reinsurance_receivables','CNY','monetary','instant'),
('reinsurance_contract_reserves_receivable','CNY','monetary','instant'),
('assets_held_for_sale','CNY','monetary','instant'),
('central_bank_borrowings','CNY','monetary','instant'),
('interbank_borrowings','CNY','monetary','instant'),
('financial_assets_sold_under_repurchase_agreements','CNY','monetary','instant'),
('fees_and_commissions_payable','CNY','monetary','instant'),
('reinsurance_payables','CNY','monetary','instant'),
('insurance_contract_reserves','CNY','monetary','instant'),
('brokerage_client_payables','CNY','monetary','instant'),
('underwriting_client_payables','CNY','monetary','instant'),
('liabilities_held_for_sale','CNY','monetary','instant'),
('provisions_current','CNY','monetary','instant'),
('deferred_income_current','CNY','monetary','instant'),
('preferred_stock_liabilities','CNY','monetary','instant'),
('perpetual_bond_liabilities','CNY','monetary','instant'),
('preferred_stock_equity','CNY','monetary','instant'),
('perpetual_bond_equity','CNY','monetary','instant'),
('other_equity_instrument_investments','CNY','monetary','instant'),
('contract_assets','CNY','monetary','instant'),
('other_assets','CNY','monetary','instant'),
('securities_business_receivables','CNY','monetary','instant'),
('deposits_paid','CNY','monetary','instant'),
('financial_cash_and_central_bank_balances','CNY','monetary','instant'),
('precious_metals','CNY','monetary','instant'),
('financial_assets_at_fair_value_through_profit_or_loss','CNY','monetary','instant'),
('agency_business_assets','CNY','monetary','instant'),
('receivable_investments','CNY','monetary','instant'),
('interbank_and_financial_institution_deposits','CNY','monetary','instant'),
('financial_liabilities_at_fair_value_through_profit_or_loss','CNY','monetary','instant'),
('customer_deposits','CNY','monetary','instant'),
('agency_business_liabilities','CNY','monetary','instant'),
('other_liabilities','CNY','monetary','instant'),
('diluted_earnings_per_share','CNY/share','per_share','ytd'),
('exchange_income','CNY','monetary','ytd'),
('earned_premiums','CNY','monetary','ytd'),
('fee_and_commission_income','CNY','monetary','ytd'),
('surrendered_premiums','CNY','monetary','ytd'),
('net_claims_paid','CNY','monetary','ytd'),
('insurance_contract_reserve_expense','CNY','monetary','ytd'),
('policyholder_dividend_expense','CNY','monetary','ytd'),
('reinsurance_expenses','CNY','monetary','ytd'),
('noncurrent_asset_disposal_gain','CNY','monetary','ytd'),
('credit_impairment_loss','CNY','monetary','ytd'),
('net_exposure_hedging_income','CNY','monetary','ytd'),
('financial_other_operating_income','CNY','monetary','ytd'),
('financial_business_and_administrative_expense','CNY','monetary','ytd'),
('financial_other_operating_cost','CNY','monetary','ytd'),
('other_cash_balance_effects','CNY','monetary','ytd'),
('customer_and_interbank_deposits_net_increase','CNY','monetary','ytd'),
('central_bank_borrowings_net_increase','CNY','monetary','ytd'),
('other_financial_institution_borrowings_net_increase','CNY','monetary','ytd'),
('insurance_premiums_cash_received','CNY','monetary','ytd'),
('reinsurance_cash_received_net','CNY','monetary','ytd'),
('policyholder_deposits_and_investments_net_increase','CNY','monetary','ytd'),
('fair_value_financial_asset_disposal_cash_net','CNY','monetary','ytd'),
('financial_interest_and_fee_cash_received','CNY','monetary','ytd'),
('interbank_borrowing_cash_net_increase','CNY','monetary','ytd'),
('repurchase_agreement_cash_net_increase','CNY','monetary','ytd'),
('customer_loans_and_advances_net_increase','CNY','monetary','ytd'),
('central_bank_and_interbank_placements_net_increase','CNY','monetary','ytd'),
('insurance_claims_cash_paid','CNY','monetary','ytd'),
('financial_interest_and_fee_cash_paid','CNY','monetary','ytd'),
('policyholder_dividends_cash_paid','CNY','monetary','ytd'),
('financial_interest_and_fee_receipts_net_increase','CNY','monetary','ytd'),
('financial_fee_cash_paid','CNY','monetary','ytd'),
('bond_issuance_cash_paid','CNY','monetary','ytd');
INSERT INTO fundamental.provider_field (source,provider_field,canonical_field,display_name,unit,value_kind,valid_from,notes,period_basis,value_multiplier,zero_policy) VALUES
('tdx','FN1','basic_earnings_per_share','基本每股收益','CNY/share','per_share','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',1,'reject'),
('tdx','FN10','notes_receivable','应收票据','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',1,'reject'),
('tdx','FN14','related_party_receivables','应收关联公司款','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',1,'reject'),
('tdx','FN15','interest_receivable','应收利息','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',1,'reject'),
('tdx','FN18','consumable_biological_assets','其中：消耗性生物资产','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',1,'reject'),
('tdx','FN22','available_for_sale_financial_assets','可供出售金融资产','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',1,'reject'),
('tdx','FN23','held_to_maturity_investments','持有至到期投资','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',1,'reject'),
('tdx','FN24','long_term_receivables','长期应收款','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',1,'reject'),
('tdx','FN29','construction_materials','工程物资','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',1,'reject'),
('tdx','FN30','fixed_assets_pending_disposal','固定资产清理','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',1,'reject'),
('tdx','FN31','productive_biological_assets','生产性生物资产','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',1,'reject'),
('tdx','FN32','oil_and_gas_assets','油气资产','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',1,'reject'),
('tdx','FN34','development_costs','开发支出','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',1,'reject'),
('tdx','FN35','goodwill','商誉','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',1,'reject'),
('tdx','FN45','advance_receipts','预收款项','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',1,'reject'),
('tdx','FN48','interest_payable','应付利息','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',1,'reject'),
('tdx','FN49','dividends_payable','应付股利','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',1,'reject'),
('tdx','FN51','related_party_payables','应付关联公司款','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',1,'reject'),
('tdx','FN57','long_term_payables','长期应付款','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',1,'reject'),
('tdx','FN58','special_payables','专项应付款','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',1,'reject'),
('tdx','FN61','other_noncurrent_liabilities','其他非流动负债','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',1,'reject'),
('tdx','FN67','treasury_stock','减：库存股','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',1,'reject'),
('tdx','FN70','foreign_currency_translation_difference','外币报表折算价差','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',1,'reject'),
('tdx','FN79','exploration_expenses','勘探费用','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',1,'reject'),
('tdx','FN81','asset_impairment_loss','资产减值损失','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',1,'reject'),
('tdx','FN85','other_operating_profit_adjustments','影响营业利润的其他科目','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',1,'reject'),
('tdx','FN87','subsidy_income','加：补贴收入','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',1,'reject'),
('tdx','FN90','noncurrent_asset_disposal_loss','其中：非流动资产处置净损失','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',1,'reject'),
('tdx','FN91','other_pretax_profit_adjustments','加：影响利润总额的其他科目','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',1,'reject'),
('tdx','FN94','other_net_income_adjustments','加：影响净利润的其他科目','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',1,'reject'),
('tdx','FN111','subsidiary_disposal_cash','处置子公司及其他营业单位收到的现金净额','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',1,'reject'),
('tdx','FN116','subsidiary_acquisition_cash','取得子公司及其他营业单位支付的现金净额','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',1,'reject'),
('tdx','FN122','other_financing_cash_received','收到其他与筹资活动有关的现金','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',1,'reject'),
('tdx','FN130','other_effects_on_cash','四(2)、其他原因对现金的影响','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',1,'reject'),
('tdx','FN132','opening_cash_and_cash_equivalents','期初现金及现金等价物余额','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','opening_instant',1,'reject'),
('tdx','FN149','other_operating_cashflow_adjustments','其他','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',1,'reject'),
('tdx','FN151','debt_converted_to_equity','债务转为资本','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',1,'reject'),
('tdx','FN152','convertible_bonds_due_within_one_year','一年内到期的可转换公司债券','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',1,'reject'),
('tdx','FN153','finance_leased_fixed_assets','融资租入固定资产','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',1,'reject'),
('tdx','FN154','closing_cash','现金的期末余额','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',1,'reject'),
('tdx','FN155','opening_cash','减：现金的期初余额','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','opening_instant',1,'reject'),
('tdx','FN156','closing_cash_equivalents','加：现金等价物的期末余额','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',1,'reject'),
('tdx','FN157','opening_cash_equivalents','减：现金等价物的期初余额','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','opening_instant',1,'reject'),
('tdx','FN268','general_risk_reserve','一般风险准备(金融类)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',1,'reject'),
('tdx','FN295','notes_and_accounts_payable','应付票据及应付账款(资产负债表)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',1,'reject'),
('tdx','FN296','notes_and_accounts_receivable','应收票据及应收账款(资产负债表)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',1,'reject'),
('tdx','FN303','discontinued_operations_net_income','终止经营净利润(利润表)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',1,'reject'),
('tdx','FN311','basic_earnings_per_share_quarter','基本每股收益（单季度）','CNY/share','per_share','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','quarter',1,'reject'),
('tdx','FN312','total_operating_revenue_quarter','营业总收入(单季度)(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','quarter',10000,'reject'),
('tdx','FN324','net_income_quarter','净利润（单季度）(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','quarter',10000,'reject'),
('tdx','FN328','cost_of_revenue_quarter','营业成本（单季度）(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','quarter',10000,'reject'),
('tdx','FN358','domestic_main_business_revenue','主营业务收入(内销)(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN359','overseas_main_business_revenue','主营业务收入(外销)(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN401','special_reserve','专项储备(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN402','settlement_reserve','结算备付金(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN404','loans_and_advances_current','发放贷款及垫款(万元)(流动资产科目)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN406','premiums_receivable','应收保费(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN407','reinsurance_receivables','应收分保账款(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN408','reinsurance_contract_reserves_receivable','应收分保合同准备金(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN410','assets_held_for_sale','划分为持有待售的资产(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN412','central_bank_borrowings','向中央银行借款(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN414','interbank_borrowings','拆入资金(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN416','financial_assets_sold_under_repurchase_agreements','卖出回购金融资产款(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN417','fees_and_commissions_payable','应付手续费及佣金(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN418','reinsurance_payables','应付分保账款(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN419','insurance_contract_reserves','保险合同准备金(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN420','brokerage_client_payables','代理买卖证券款(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN421','underwriting_client_payables','代理承销证券款(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN422','liabilities_held_for_sale','划分为持有待售的负债(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN423','provisions_current','预计负债(万元) （流动负债）','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN424','deferred_income_current','递延收益(万元)（流动负债科目，公告此科目的股票较少，大部分公司没有此数据）','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN425','preferred_stock_liabilities','其中:优先股(万元)(非流动负债科目)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN426','perpetual_bond_liabilities','永续债(万元)(非流动负债科目)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN428','preferred_stock_equity','其中:优先股(万元)(所有者权益科目)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN429','perpetual_bond_equity','永续债(万元)(所有者权益科目)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN432','other_equity_instrument_investments','其他权益工具投资(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN435','contract_assets','合同资产(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN436','other_assets','其他资产(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN441','securities_business_receivables','应收款项(万元)  [注：证券类指标]','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN442','deposits_paid','存出保证金(万元)  [注：证券类指标]','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN443','financial_cash_and_central_bank_balances','现金及存放中央银行款项(万元)  [注：金融类科目]','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN444','precious_metals','贵金属(万元)  [注：金融类科目]','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN445','financial_assets_at_fair_value_through_profit_or_loss','以公允价值计量且其变动计入当期损益的金融资产(万元)  [注：金融类科目]','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN446','agency_business_assets','代理业务资产(万元)  [注：金融类科目]','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN447','receivable_investments','应收款项类投资(万元)  [注：金融类科目]','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN448','interbank_and_financial_institution_deposits','同业及其它金融机构存放款项(万元)  [注：金融类科目]','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN449','financial_liabilities_at_fair_value_through_profit_or_loss','以公允价值计量且其变动计入当期损益的金融负债(万元)  [注：金融类科目]','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN450','customer_deposits','吸收存款(万元)  [注：金融类科目]','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN451','agency_business_liabilities','代理业务负债(万元)  [注：金融类科目]','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN452','other_liabilities','其他负债(万元)  [注：金融类科目]','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','instant',10000,'reject'),
('tdx','FN501','diluted_earnings_per_share','稀释每股收益(元)','CNY/share','per_share','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',1,'reject'),
('tdx','FN503','exchange_income','汇兑收益(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN507','earned_premiums','已赚保费(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN508','fee_and_commission_income','手续费及佣金收入(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN511','surrendered_premiums','退保金(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN512','net_claims_paid','赔付支出净额(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN513','insurance_contract_reserve_expense','提取保险合同准备金净额(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN514','policyholder_dividend_expense','保单红利支出(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN515','reinsurance_expenses','分保费用(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN516','noncurrent_asset_disposal_gain','其中:非流动资产处置利得(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN517','credit_impairment_loss','信用减值损失(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN518','net_exposure_hedging_income','净敞口套期收益(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN522','financial_other_operating_income','其他业务收入(万元)  [注：金融类科目]','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN523','financial_business_and_administrative_expense','业务及管理费(万元)  [注：金融类科目]','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN524','financial_other_operating_cost','其他业务成本(万元)  [注：金融类科目]','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN561','other_cash_balance_effects','加:其他原因对现金的影响2(万元)(现金的期末余额科目)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN562','customer_and_interbank_deposits_net_increase','客户存款和同业存放款项净增加额(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN563','central_bank_borrowings_net_increase','向中央银行借款净增加额(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN564','other_financial_institution_borrowings_net_increase','向其他金融机构拆入资金净增加额(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN565','insurance_premiums_cash_received','收到原保险合同保费取得的现金(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN566','reinsurance_cash_received_net','收到再保险业务现金净额(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN567','policyholder_deposits_and_investments_net_increase','保户储金及投资款净增加额(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN568','fair_value_financial_asset_disposal_cash_net','处置以公允价值计量且其变动计入当期损益的金融资产净增加额(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN569','financial_interest_and_fee_cash_received','收取利息、手续费及佣金的现金(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN570','interbank_borrowing_cash_net_increase','拆入资金净增加额(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN571','repurchase_agreement_cash_net_increase','回购业务资金净增加额(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN572','customer_loans_and_advances_net_increase','客户贷款及垫款净增加额(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN573','central_bank_and_interbank_placements_net_increase','存放中央银行和同业款项净增加额(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN574','insurance_claims_cash_paid','支付原保险合同赔付款项的现金(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN575','financial_interest_and_fee_cash_paid','支付利息、手续费及佣金的现金(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN576','policyholder_dividends_cash_paid','支付保单红利的现金(万元)','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN582','financial_interest_and_fee_receipts_net_increase','收取利息和手续费净增加额(万元)  [注：金融类科目]','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN583','financial_fee_cash_paid','支付手续费的现金(万元)  [注：金融类科目]','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject'),
('tdx','FN584','bond_issuance_cash_paid','发行债券支付的现金(万元)  [注：金融类科目]','CNY','monetary','1900-01-01','official-statements-20260919; documented source semantics; no individual company PDF certification','ytd',10000,'reject');

-- Standard report snapshot: values only from approved, materialized facts.
-- Unreviewed/missing rows remain in the statement denominator; no source zero
-- becomes a reported zero. Evidence is opt-in for ordinary business queries.
CREATE MACRO fundamental.statements_asof(as_of_time, report_end, security_code, include_evidence := false) AS TABLE (
 WITH identity_candidates AS (
  SELECT DISTINCT x.instrument_id,x.identifier_value
  FROM core.instrument_identifier x JOIN core.instrument i USING(instrument_id)
  WHERE x.provider='tdx' AND x.identifier_type='symbol' AND i.instrument_type<>'index'
    AND right(x.identifier_value,6)=security_code
    AND (x.valid_from IS NULL OR x.valid_from<=CAST(report_end AS DATE))
    AND (x.valid_to IS NULL OR x.valid_to>CAST(report_end AS DATE))
 ), identity AS (
  SELECT CASE WHEN count(*)=1 THEN min(instrument_id) END AS instrument_id,
   CASE WHEN count(*)=1 THEN 'resolved' WHEN count(*)=0 THEN 'unresolved_identity' ELSE 'ambiguous_identity' END AS identity_status
  FROM identity_candidates
 ), catalog AS (
  SELECT name AS field,min(display_name) AS label,statement,section,unit,value_kind,period_basis,
   mapping_status,review_reason,count(*) AS source_variants
  FROM fundamental.source_field WHERE source='tdx' AND statement<>'' AND name IS NOT NULL
  GROUP BY name,statement,section,unit,value_kind,period_basis,mapping_status,review_reason
 ), visible_source AS (
  SELECT s.name,p.provider_fact_id,p.provider_field,p.value,p.value_float32_bits,p.artifact_id,
   p.revision_key,p.source_file_hash,l.filing_id,f.announcement_time,
   row_number() OVER (PARTITION BY s.name ORDER BY f.announcement_time DESC,p.provider_fact_id DESC) AS rank
  FROM fundamental.provider_fact p
  JOIN fundamental.source_field s ON s.source=p.source AND s.provider_field=p.provider_field
  JOIN fundamental.provider_filing_link l ON l.provider_source=p.source AND l.provider_revision_key=p.revision_key AND l.provider_code=p.provider_code AND l.status='linked'
  JOIN fundamental.filing f ON f.filing_id=l.filing_id AND f.resolution_status='resolved' AND f.instrument_id=p.instrument_id AND f.report_period=p.report_period
  CROSS JOIN identity i
  WHERE p.source='tdx' AND p.provider_code=security_code AND p.report_period=CAST(report_end AS DATE)
    AND p.instrument_id=i.instrument_id AND f.announcement_time<=CAST(as_of_time AS TIMESTAMPTZ)
 ), mappings AS (
  SELECT DISTINCT m.canonical_field,m.provider_field,m.valid_from,m.valid_to
  FROM fundamental.provider_field m
  JOIN fundamental.field f ON f.canonical_field=m.canonical_field AND f.unit=m.unit AND f.value_kind=m.value_kind AND f.period_basis=m.period_basis
  JOIN fundamental.source_field s ON s.source=m.source AND s.provider_field=m.provider_field AND s.name=m.canonical_field AND s.unit=m.unit AND s.value_multiplier=m.value_multiplier AND s.period_basis=m.period_basis
  WHERE m.source='tdx' AND m.value_multiplier IN (1,10000)
    AND m.valid_from<=CAST(report_end AS DATE) AND (m.valid_to IS NULL OR CAST(report_end AS DATE)<m.valid_to)
 ), facts AS (
  SELECT f.* FROM fundamental.fact_asof(CAST(as_of_time AS TIMESTAMPTZ)) f CROSS JOIN identity i
  WHERE f.instrument_id=i.instrument_id AND f.report_period=CAST(report_end AS DATE) AND f.materializer_version<>'legacy'
 ), evaluated AS (
  SELECT i.instrument_id,i.identity_status,c.*,f.fact_id,f.value,f.period_type,f.currency,f.statement_scope,f.announcement_time,
   f.source_filing_id,f.provider_fact_id,f.source_provider_field,f.revision_key,
   v.provider_fact_id AS diagnostic_provider_fact_id,v.filing_id AS diagnostic_filing_id,
   CASE
    WHEN i.identity_status<>'resolved' THEN i.identity_status
    WHEN EXISTS(SELECT 1 FROM fundamental.provider_conflicts_asof(as_of_time) conflict WHERE conflict.source='tdx' AND conflict.provider_code=security_code AND conflict.report_period=CAST(report_end AS DATE)) THEN 'source_conflict'
    WHEN c.mapping_status NOT IN ('reviewed_mapping','official_mapping') THEN c.review_reason
    WHEN c.source_variants<>1 THEN 'ambiguous_source_variant'
    WHEN NOT EXISTS(SELECT 1 FROM mappings m WHERE m.canonical_field=c.field) THEN 'mapping_unavailable_for_period'
    WHEN f.fact_id IS NOT NULL AND f.unit=c.unit AND f.statement_scope='provider_default'
      AND f.currency IS NOT DISTINCT FROM CASE WHEN c.value_kind IN ('monetary','per_share') THEN 'CNY' END
      AND f.period_type=CASE WHEN c.period_basis IN ('instant','opening_instant') THEN c.period_basis
        WHEN c.period_basis='quarter' THEN 'Q'||CAST(quarter(CAST(report_end AS DATE)) AS VARCHAR)
        WHEN month(CAST(report_end AS DATE))=3 THEN 'Q1' WHEN month(CAST(report_end AS DATE))=6 THEN 'H1'
        WHEN month(CAST(report_end AS DATE))=9 THEN '9M' ELSE 'FY' END
      AND EXISTS(SELECT 1 FROM mappings m WHERE m.canonical_field=c.field AND m.provider_field=f.source_provider_field)
      THEN 'available'
    WHEN f.fact_id IS NOT NULL THEN 'standard_fact_semantics_mismatch'
    WHEN v.provider_fact_id IS NULL THEN 'no_linked_source_at_asof'
    WHEN v.value IS NULL OR NOT isfinite(v.value) THEN 'invalid_source_value'
    WHEN v.value=0 THEN 'source_zero_ambiguous'
    ELSE 'not_materialized'
   END AS status
  FROM catalog c CROSS JOIN identity i
  LEFT JOIN facts f ON f.canonical_field=c.field AND f.provider_code=security_code AND f.primary_source='tdx'
  LEFT JOIN visible_source v ON v.name=c.field AND v.rank=1
 )
 SELECT instrument_id,identity_status,security_code AS code,CAST(report_end AS DATE) AS report_period,
  CAST(as_of_time AS TIMESTAMPTZ) AS information_as_of,statement,section,field,label,unit,
  CASE WHEN value_kind IN ('monetary','per_share') THEN 'CNY' END AS currency,
  period_basis,CASE WHEN period_basis='opening_instant' THEN make_date(year(CAST(report_end AS DATE))-1,12,31)
    WHEN period_basis='instant' THEN CAST(report_end AS DATE) END AS balance_date,
  mapping_status AS mapping_review,review_reason,status,
  CASE WHEN status='available' THEN value END AS value,
  CASE WHEN status='available' THEN announcement_time END AS announcement_time,
  CASE WHEN include_evidence THEN to_json(struct_pack(
    fact_id:=CASE WHEN status='available' THEN fact_id END,
    provider_fact_id:=coalesce(provider_fact_id,diagnostic_provider_fact_id),
    filing_id:=coalesce(source_filing_id,diagnostic_filing_id),
    provider_field:=source_provider_field,source_revision:=revision_key)) END AS source_evidence
 FROM evaluated
 WHERE CAST(report_end AS DATE)=last_day(CAST(report_end AS DATE)) AND month(CAST(report_end AS DATE)) IN (3,6,9,12)
);
