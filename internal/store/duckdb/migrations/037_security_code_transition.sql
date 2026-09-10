-- 官方来源代码关系；不推断标准证券ID或原始挂牌列的交易所身份区间。
CREATE SEQUENCE reference.security_code_transition_id_seq START 1;
CREATE TABLE reference.security_code_transition (
    observation_id BIGINT PRIMARY KEY DEFAULT nextval('reference.security_code_transition_id_seq'),
    release_id BIGINT NOT NULL,
    artifact_id BIGINT NOT NULL,
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    exchange_mic VARCHAR NOT NULL CHECK (exchange_mic='XBSE'),
    old_code VARCHAR NOT NULL CHECK (regexp_full_match(old_code,'[0-9]{6}')),
    new_code VARCHAR NOT NULL CHECK (regexp_full_match(new_code,'920[0-9]{3}') AND new_code<>old_code),
    source_name VARCHAR NOT NULL CHECK (length(trim(source_name))>0),
    source_listing_date VARCHAR NOT NULL,
    switch_date DATE NOT NULL,
    UNIQUE(release_id, old_code),
    UNIQUE(release_id, new_code),
    UNIQUE(release_id, source_row)
);
