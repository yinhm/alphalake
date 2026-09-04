# ADR 006 — TDX professional financial artifacts and provider facts

Status: accepted

## Context

AlphaLake's target design requires immutable raw evidence, lossless provider facts, and point-in-time financial semantics. TDX professional financial data is distributed through `tdxfin/gpcw.txt` plus versioned `gpcwYYYYMMDD.zip` packages. The package payload is a binary float32 matrix and its header carries the report period and record width; it does not, by itself, provide an authoritative announcement timestamp for each company record.

This is the first production slice that exercises `meta.artifact` and `fundamental.provider_fact`.

## Decision 1 — Raw bytes are immutable evidence

Every fetched manifest and package is retained through the common artifact store before semantic ingestion.

Artifacts are:

- content-addressed by SHA-256;
- written atomically to disk;
- verified when reloaded;
- recorded in `meta.artifact` with source locator, fetch time, content length, parser version, and ingest-run lineage;
- root-relative on disk so the database and raw lake can be moved together.

The same bytes from different provider locators share one physical content-addressed file but keep separate metadata lineage rows.

A package that cannot yet be fully canonicalized remains useful evidence and is retried from local raw bytes rather than redownloaded.

## Decision 2 — Parse gpcw dynamically and losslessly

The gpcw parser derives field count from the binary header's `report_size / 4`. It must never hardcode a historical field count such as 264.

Each provider field retains:

- its stable ordinal name (`FN1`, `FN2`, ...);
- the exact float32 bit pattern;
- an exactly representable float64 analytical value.

Preserving the raw bits matters for signed zero, NaN payloads, and reproducible provider decoding even when analytical consumers normally use the float64 value.

## Decision 3 — Artifact content is a provider-fact revision

`fundamental.provider_fact.revision_key` is the artifact SHA-256.

Consequences:

- replaying the same immutable artifact is idempotent;
- a corrected package for the same report period produces a distinct revision instead of overwriting prior evidence;
- old revisions remain queryable and traceable to their raw package.

Migration 009 adds `value_float32_bits` and hardens the provider-field catalog identity before the first production financial writer.

## Decision 4 — Financial identity is resolved as of report period

A gpcw record's six-digit code is not a canonical identity. AlphaLake normalizes it to a TDX provider identifier and resolves that identifier using temporal `[valid_from, valid_to)` semantics at the report period.

If a historical record cannot be resolved, AlphaLake does not invent a canonical instrument. Resolved records may be ingested, while the package remains incomplete and therefore does not receive a completion checkpoint. A later lifecycle enrichment can then replay the retained artifact and ingest the previously unresolved record.

## Decision 5 — Do not invent announcement time

The raw gpcw package parser provides report period but no authoritative per-record announcement timestamp. Fetch time, package filename, and report period are not acceptable substitutes.

Therefore this slice writes provider facts with nullable `announcement_time` and deliberately does **not** materialize `fundamental.fact`, whose point-in-time contract requires an announcement timestamp.

Canonical PIT facts become eligible only after announcement time is enriched from a verified source, such as authoritative filing metadata or another provider interface whose semantics have been validated.

## Decision 6 — Safe CLI default

`sync-financial <db>` processes only the newest listed package by default. `sync-financial <db> --all` explicitly requests the full historical package set.

This prevents an ordinary refresh command from unexpectedly triggering a very large historical download and fact expansion.

Raw files default to a `raw/` directory beside the DuckDB file.

## Consequences

- D-008 immutable raw artifacts now has a production implementation rather than schema-only intent.
- Provider-level financial history can be replayed and revised without losing source evidence.
- Canonical PIT facts remain intentionally incomplete until announcement-time semantics are authoritative.
- Historical delisted/security-code edge cases fail conservatively instead of silently merging facts into the wrong instrument.
