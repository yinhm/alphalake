-- First production fundamental writer migration.
-- TDX professional-financial records are binary float32 values. value DOUBLE
-- remains convenient for analysis because every finite float32 is exactly
-- representable as float64, while value_float32_bits preserves the provider
-- payload losslessly (including signed zero / NaN payloads).
ALTER TABLE fundamental.provider_fact
ADD COLUMN IF NOT EXISTS value_float32_bits UBIGINT;

-- The v0 provider_field primary key included nullable valid_from. Rebuild it
-- with an explicit open-ended lower sentinel so field-catalog versions have a
-- deterministic non-null identity.
ALTER TABLE fundamental.provider_field RENAME TO provider_field_legacy;

CREATE TABLE fundamental.provider_field (
    source VARCHAR NOT NULL,
    provider_field VARCHAR NOT NULL,
    canonical_field VARCHAR,
    display_name VARCHAR,
    unit VARCHAR,
    value_kind VARCHAR,
    valid_from DATE NOT NULL DEFAULT DATE '1900-01-01',
    valid_to DATE,
    notes VARCHAR,
    PRIMARY KEY(source, provider_field, valid_from)
);

INSERT INTO fundamental.provider_field (
    source, provider_field, canonical_field, display_name,
    unit, value_kind, valid_from, valid_to, notes
)
SELECT
    source, provider_field, canonical_field, display_name,
    unit, value_kind, COALESCE(valid_from, DATE '1900-01-01'), valid_to, notes
FROM fundamental.provider_field_legacy;

DROP TABLE fundamental.provider_field_legacy;
