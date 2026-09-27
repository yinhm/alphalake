-- Reviewed disclosure scope only: numeric values remain in TDX archives.
CREATE TABLE fundamental.filing_coverage_review (
 review_sha256 VARCHAR PRIMARY KEY,
 filing_id BIGINT NOT NULL,
 review_artifact_id BIGINT NOT NULL,
 supersedes_sha256 VARCHAR UNIQUE,
 reviewed_record VARCHAR NOT NULL CHECK(json_valid(reviewed_record))
);
CREATE VIEW fundamental.active_filing_coverage AS
 SELECT r.filing_id,CAST(r.reviewed_record::JSON->>'period' AS DATE) AS report_period,
 from_json(r.reviewed_record::JSON->'fields','["VARCHAR"]') AS fields,
 r.review_sha256
 FROM fundamental.filing_coverage_review r
 JOIN meta.artifact a ON a.artifact_id=r.review_artifact_id
  AND a.source='disclosure-review' AND a.dataset='filing_period_coverage'
  AND a.sha256=r.review_sha256 AND r.review_sha256=sha256(r.reviewed_record)
 JOIN fundamental.filing f ON f.filing_id=r.filing_id
  AND f.sha256=(r.reviewed_record::JSON->>'pdf_sha256')
 JOIN meta.artifact d ON d.artifact_id=f.artifact_id AND d.sha256=f.sha256
 WHERE (r.reviewed_record::JSON->>'action')='publish'
 AND f.source='cninfo' AND f.filing_type='prospectus' AND f.filing_variant='full'
 AND f.resolution_status='resolved' AND f.instrument_id IS NOT NULL
 AND NOT EXISTS(SELECT 1 FROM fundamental.filing_coverage_review next WHERE next.supersedes_sha256=r.review_sha256);
