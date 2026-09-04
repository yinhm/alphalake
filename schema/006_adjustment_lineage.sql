ALTER TABLE market.adjustment_segment
ADD COLUMN IF NOT EXISTS ingest_run_id BIGINT;
