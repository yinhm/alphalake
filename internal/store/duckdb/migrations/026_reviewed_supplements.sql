-- 人工审核的 CNINFO 附注独立保存，不覆盖 TDX 标准事实或存放估值政策。
CREATE TABLE fundamental.reviewed_supplement (
    provider_code VARCHAR NOT NULL,
    report_period DATE NOT NULL,
    item VARCHAR NOT NULL,
    value DECIMAL(38,10) NOT NULL,
    unit VARCHAR NOT NULL CHECK (unit IN ('CNY', 'CNY/share')),
    period_basis VARCHAR NOT NULL CHECK (period_basis IN ('instant', 'ytd')),
    statement_scope VARCHAR NOT NULL,
    source_filing_id BIGINT NOT NULL,
    pdf_sha256 VARCHAR NOT NULL,
    pdf_page INTEGER NOT NULL CHECK (pdf_page > 0),
    reviewer VARCHAR NOT NULL,
    review_note VARCHAR NOT NULL,
    import_sha256 VARCHAR NOT NULL,
    reviewed_record VARCHAR NOT NULL,
    PRIMARY KEY(provider_code, report_period, item, source_filing_id)
);
