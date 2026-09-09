-- 首次观测保持不变；最近一次实际观察用于当前行业政策的新鲜度检查。
-- 旧关闭区间的 ingest_run_id 已代表关闭运行，不能据此补造首次发布证据。
ALTER TABLE classification.membership ADD COLUMN last_observed_at TIMESTAMPTZ;
ALTER TABLE classification.membership ADD COLUMN last_observed_run_id BIGINT;
UPDATE classification.membership
SET last_observed_at=observed_at,last_observed_run_id=ingest_run_id
WHERE effective_to IS NULL;
