-- 发行人对募资净额的披露估计。上市日不是现金结算日，金额不回写财务事实。
CREATE SEQUENCE market.equity_proceeds_id_seq START 1;
CREATE TABLE market.equity_proceeds_observation (
 observation_id BIGINT PRIMARY KEY DEFAULT nextval('market.equity_proceeds_id_seq'),
 release_id BIGINT NOT NULL,
 artifact_id BIGINT NOT NULL,
 company_id BIGINT NOT NULL,
 event_code VARCHAR NOT NULL CHECK(event_code IN ('ipo','greenshoe')),
 listing_date DATE NOT NULL,
 date_status VARCHAR NOT NULL CHECK(date_status IN ('reported_listing_date_not_cash_settlement','expected_listing_date_not_cash_settlement')),
 issued_shares DECIMAL(38,0) NOT NULL CHECK(issued_shares>0),
 currency VARCHAR NOT NULL CHECK(regexp_full_match(currency,'[A-Z]{3}')),
 net_proceeds DECIMAL(38,10) NOT NULL CHECK(net_proceeds>0),
 amount_status VARCHAR NOT NULL CHECK(amount_status='issuer_estimate_after_estimated_costs'),
 source_locator VARCHAR NOT NULL CHECK(length(trim(source_locator))>0),
 UNIQUE(release_id,event_code)
);
