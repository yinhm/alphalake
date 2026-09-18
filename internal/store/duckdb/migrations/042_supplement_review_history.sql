-- 当前表是审核头部投影，完整原值及审核动作另行保留。
ALTER TABLE fundamental.reviewed_supplement ADD COLUMN review_state VARCHAR DEFAULT 'active';
CREATE TABLE fundamental.supplement_review_history (
 import_sha256 VARCHAR PRIMARY KEY,
 provider_code VARCHAR NOT NULL,
 report_period DATE NOT NULL,
 item VARCHAR NOT NULL,
 source_filing_id BIGINT NOT NULL,
 action VARCHAR NOT NULL CHECK(action IN ('publish','replace','revoke')),
 supersedes_sha256 VARCHAR,
 reviewed_at TIMESTAMPTZ,
 recorded_at TIMESTAMPTZ,
 reviewed_record VARCHAR NOT NULL CHECK(json_valid(reviewed_record)),
 CHECK((action='publish' AND supersedes_sha256 IS NULL) OR
       (action<>'publish' AND supersedes_sha256 IS NOT NULL))
);
INSERT INTO fundamental.supplement_review_history
SELECT import_sha256,provider_code,report_period,item,source_filing_id,'publish',NULL,NULL,NULL,reviewed_record
FROM fundamental.reviewed_supplement;
