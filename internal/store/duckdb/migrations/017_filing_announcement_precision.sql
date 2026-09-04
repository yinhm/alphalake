-- The public CNINFO catalogue exposes an announcement date through its
-- announcementTime field but does not establish a trustworthy intraday release
-- instant. Preserve the provider milliseconds separately and record the
-- canonical disclosure date/availability precision explicitly.
ALTER TABLE fundamental.filing ADD COLUMN announcement_date DATE;
ALTER TABLE fundamental.filing ADD COLUMN announcement_time_precision VARCHAR NOT NULL DEFAULT 'timestamp';

-- No production CNINFO writer existed before this migration. This fallback only
-- gives manually-created legacy rows a deterministic date; the production writer
-- supplies the exchange-local disclosure date explicitly.
UPDATE fundamental.filing
SET announcement_date=CAST(announcement_time AS DATE)
WHERE announcement_date IS NULL AND announcement_time IS NOT NULL;
