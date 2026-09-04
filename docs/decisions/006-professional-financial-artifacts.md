# ADR 006 — TDX professional financial artifacts and provider facts

Status: accepted

## Context

AlphaLake's target design requires immutable raw evidence, lossless provider facts, and point-in-time financial semantics. TDX professional financial data is distributed through `tdxfin/gpcw.txt` plus versioned `gpcwYYYYMMDD.zip` packages. The package payload is a binary float32 matrix and its header carries the report period and record width; it does not, by itself, provide an authoritative announcement timestamp for each company record.

This is the first production slice that exercises `meta.artifact` and `fundamental.provider_fact`.

## Decision 1 — Raw bytes are immutable evidence and retained corruption is recoverable

Every fetched manifest and package is retained through the common artifact store before semantic ingestion.

Artifacts are:

- content-addressed by SHA-256;
- written atomically to disk;
- verified when reloaded;
- recorded in `meta.artifact` with source locator, fetch time, content length, parser version, and ingest-run lineage;
- root-relative on disk so the database and raw lake can be moved together.

The same bytes from different provider locators share one physical content-addressed file but keep separate metadata lineage rows.

A package that cannot yet be fully canonicalized remains useful evidence and is retried from local raw bytes rather than redownloaded. All retained versions for a locator are eligible for reuse: if an upstream manifest later rolls back from content B to previously-seen content A, AlphaLake verifies and reuses local A rather than unnecessarily fetching it again.

Retained artifacts are a cache of immutable evidence, not a source of permanent wedges. Strict artifact reads still surface missing/corrupt bytes as errors for diagnostics. Financial acquisition uses a healthy-version scan instead: corrupt retained revisions are skipped, and if no healthy local revision matches the provider manifest the package is redownloaded through the provider integrity path. The downloaded bytes are rechecked against manifest size/MD5 before `Persist` atomically repairs a corrupt content-addressed file while preserving its artifact metadata identity.

## Decision 2 — Parse gpcw dynamically and losslessly

The gpcw parser derives field count from the binary header's `report_size / 4`. It must never hardcode a historical field count such as 264.

Each provider field retains:

- its stable ordinal name (`FN1`, `FN2`, ...);
- the exact float32 bit pattern;
- an exactly representable float64 analytical value.

Preserving the raw bits matters for signed zero, NaN payloads, and reproducible provider decoding even when analytical consumers normally use the float64 value.

`github.com/injoyai/tdx` v0.0.87 provides report-file transport (`GetReportFile`) but does not provide a professional-financial gpcw binary codec. AlphaLake therefore owns this narrowly-scoped lossless codec today. This is an explicit exception to the general preference in D-003 to reuse/upstream provider codecs; if the codec stabilizes and fits upstream scope, contributing it upstream (or maintaining the smallest possible fork) is preferred to unconstrained protocol duplication.

## Decision 3 — Artifact content is a provider revision; raw record identity is stable across canonical corrections

`fundamental.provider_fact.revision_key` is the artifact SHA-256.

Consequences:

- replaying the same immutable artifact is idempotent;
- a corrected package for the same report period produces a distinct revision instead of overwriting prior evidence;
- old revisions remain queryable and traceable to their raw package.

Migration 009 adds `value_float32_bits` and hardens the provider-field catalog identity before the first production financial writer.

Migration 012 corrects the provider-fact identity model. A provider fact is identified by immutable provider evidence — source/artifact revision + raw provider code + provider field — not by the current canonical `instrument_id`. `instrument_id` is a resolvable attribute that may change when later lifecycle evidence corrects historical identity.

The provider-fact reconcile path therefore:

- inserts facts not yet seen for an artifact/raw-code/FN tuple;
- reassigns existing facts to a newly-correct canonical instrument without creating a second fact for the same provider evidence;
- removes facts for raw records that become unresolved on replay so stale canonical links do not survive;
- keeps different artifact SHA revisions separate.

Operational counters distinguish `attempted`, `inserted`, `reassigned`, and `removed`. An idempotent replay reports no new changes instead of looking like fresh data.

## Decision 4 — Raw provider identity is not current-market classification

A gpcw record carries a six-digit provider code and a one-byte marker. Neither is a canonical instrument identity.

The source adapter preserves both values as raw evidence. It does **not** call current SDK code-range classifiers to invent `sh`/`sz`/`bj` prefixes. Current code-range rules are not a historical market master and are known to be unsuitable for old B-share and legacy Beijing/NEEQ-era records.

The one-byte package marker is retained as `MarketMarker`, but AlphaLake does not assign exchange semantics to it until those semantics are independently verified. An apparently market-like byte is not sufficient evidence to alter canonical identity.

Financial identity resolution instead queries temporal TDX `symbol` identifiers as of the package report period and groups them by the raw six-digit code. The candidate universe also applies the dataset's own semantics: gpcw contains company financial records, so index instruments are not legitimate identity candidates even when an index and a company security share the same six-digit code.

- exactly one active non-index provider symbol resolves to its canonical `instrument_id`;
- no active non-index provider symbol remains unresolved;
- multiple distinct active non-index provider symbols remain unresolved rather than guessed;
- overlapping rows for the same full provider identifier are still treated as store corruption.

For example, `sh000001` (Shanghai Composite index) does not make gpcw raw code `000001` ambiguous with `sz000001` (Ping An Bank), because the index is outside the financial-record candidate universe. A true company-security/company-security collision remains unresolved.

Thus a record that cannot currently be classified/resolved does not fail its entire package.

## Decision 5 — Durable unresolved evidence and reversible explicit acknowledgement

Migration 011 adds `fundamental.provider_record_resolution`. Every parsed provider record has durable identity-resolution state keyed by immutable artifact revision and raw provider code.

Statuses are:

- `resolved` — linked to a canonical instrument and provider identifier;
- `pending` — no unique temporal identity is currently supportable;
- `acknowledged` — an operator explicitly accepts that the record cannot presently be resolved, with a required reason and timestamp.

Replay of a still-unresolved acknowledged record preserves both the acknowledgement and the machine reason that the operator actually reviewed; later machine explanations do not silently rewrite that historical review context. If authoritative lifecycle evidence later makes the record resolvable, `resolved` supersedes the prior acknowledgement and clears obsolete acknowledgement metadata.

Acknowledgement is reversible. `financial-unack` returns an acknowledged record to `pending`, clears acknowledgement metadata, and invalidates the package completion checkpoint in the same transaction so the next `sync-financial` must replay and re-evaluate the raw record.

A package completion checkpoint means every record is either `resolved` or explicitly `acknowledged`. Pending records keep the package replayable from retained raw bytes. AlphaLake never automatically acknowledges or silently drops an unresolved record.

## Decision 6 — Do not invent announcement time

The raw gpcw package parser provides report period but no authoritative per-record announcement timestamp. Fetch time, package filename, and report period are not acceptable substitutes.

Therefore this slice writes provider facts with nullable `announcement_time` and deliberately does **not** materialize `fundamental.fact`, whose point-in-time contract requires an announcement timestamp.

Canonical PIT facts become eligible only after announcement time is enriched from a verified source, such as authoritative filing metadata or another provider interface whose semantics have been validated.

## Decision 7 — Safe CLI defaults and complete governance commands

`sync-financial <db>` processes only the newest listed package by default. `sync-financial <db> --all` explicitly requests the full historical package set.

This prevents an ordinary refresh command from unexpectedly triggering a very large historical download and fact expansion.

Raw files default to a `raw/` directory beside the DuckDB file.

Pending records are inspectable with paged `financial-unresolved <db> [--limit N] [--offset N]`. An explicit manual disposition requires `financial-ack <db> <artifact-id> <provider-code> <reason>`; the reason is mandatory. A mistaken acknowledgement can be reversed with `financial-unack <db> <artifact-id> <provider-code>`.

## Consequences

- D-008 immutable raw artifacts now has a production implementation rather than schema-only intent.
- Provider-level financial history can be replayed and revised without losing source evidence.
- Corrupt/missing retained package bytes do not permanently wedge financial ingestion; verified provider re-download can repair the local content-addressed object.
- Historical code-range gaps no longer hard-fail an entire gpcw package.
- Index/company raw-code collisions are resolved using dataset semantics instead of becoming recurring false ambiguities.
- Canonical identity corrections do not leave duplicate facts for the same immutable provider revision.
- Permanently unresolved historical evidence has a visible, auditable, reversible governance path instead of making every full-history run permanently partial.
- Canonical PIT facts remain intentionally incomplete until announcement-time semantics are authoritative.
- Provider market-marker semantics remain an explicit research item rather than an assumption embedded in identity.
