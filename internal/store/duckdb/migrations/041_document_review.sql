-- 长期审核证据独立于诊断；旧记录时间未知，不借迁移时间伪造首次记录。
CREATE TABLE fundamental.document_review (
 filing_id BIGINT NOT NULL,
 document_artifact_id BIGINT NOT NULL,
 review_artifact_id BIGINT NOT NULL,
 pdf_sha256 VARCHAR NOT NULL,
 reviewed_at TIMESTAMPTZ NOT NULL,
 recorded_at TIMESTAMPTZ,
 reviewed_record VARCHAR NOT NULL CHECK(json_valid(reviewed_record)),
 PRIMARY KEY(filing_id,document_artifact_id)
);

-- 旧诊断必须有匹配的不可变审核 JSON 和 PDF；缺证或重复时迁移失败。
CREATE TEMP TABLE document_review_upgrade AS
SELECT f.filing_id,f.artifact_id AS document_artifact_id,a.artifact_id AS review_artifact_id,
 f.sha256 AS pdf_sha256,v.checked_at AS reviewed_at,v.details AS reviewed_record
FROM meta.validation_result v
LEFT JOIN fundamental.filing f ON CAST(f.filing_id AS VARCHAR)=v.subject_key
LEFT JOIN meta.artifact a ON a.source='document-review' AND a.dataset='filing_document_binding'
 AND a.sha256=sha256(v.details) AND a.source_locator=f.source_url
WHERE v.source='document-review' AND v.dataset='filing_document'
 AND v.rule_code='reviewed_mirror_binding' AND v.passed=true;
SELECT CASE WHEN EXISTS (
 SELECT 1 FROM document_review_upgrade
 WHERE filing_id IS NULL OR document_artifact_id IS NULL OR review_artifact_id IS NULL
 OR pdf_sha256 IS NULL OR json_extract_string(reviewed_record,'$.sha256') IS DISTINCT FROM pdf_sha256
 OR NOT EXISTS (SELECT 1 FROM meta.artifact p WHERE p.artifact_id=document_artifact_id AND p.sha256=pdf_sha256)
) THEN error('legacy document review missing matching evidence') ELSE 1 END;
INSERT INTO fundamental.document_review
SELECT filing_id,document_artifact_id,review_artifact_id,pdf_sha256,reviewed_at,NULL,reviewed_record
FROM document_review_upgrade;
DROP TABLE document_review_upgrade;
