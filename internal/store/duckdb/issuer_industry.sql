CREATE TABLE reference.issuer_industry_review (
 artifact_id BIGINT PRIMARY KEY,
 recorded_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT current_timestamp,
 workbook_sha256 VARCHAR NOT NULL CHECK(regexp_full_match(workbook_sha256,'[0-9a-f]{64}')),
 verified_record VARCHAR NOT NULL CHECK(json_valid(verified_record))
);
