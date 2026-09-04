# ADR 004 — Security-master lifecycle and content-based derived dirtiness

Status: accepted

## Context

Two mechanisms existed in AlphaLake but were not effective in normal production workflows:

1. `ref.instrument_identifier` supported temporal validity, but current TDX security-master refreshes only upserted identifiers. A code disappearing from `GetCodeAll` was never closed, so a later code reuse could still resolve to the old `instrument_id`.
2. adjustment dirty state used ingestion lineage (`ingest_run_id`, `ingested_at`, row sequence IDs). Normal incremental daily sync rewrites the inclusive boundary row and GBBQ sync replaces a full snapshot, so unchanged canonical content still appeared dirty after every normal refresh.

## Decision 1 — Treat the provider security master as a verified snapshot

TDX exposes the current security universe as Shanghai, Shenzhen, and Beijing code-list partitions. The adapter emits a provider-neutral `InstrumentMasterSnapshot` with an observation date and a completeness flag.

Production TDX acquisition requires every configured exchange partition to succeed and be non-empty. The DuckDB store applies a complete snapshot in one transaction:

1. validate and upsert every current observation;
2. compare current identifiers with previously open primary provider identifiers;
3. reject suspicious exchange-level truncation before making lifecycle changes;
4. close identifiers absent from the verified complete snapshot by setting `valid_to = snapshot_date`.

Incomplete snapshots may update observations only through explicitly non-destructive fallback paths; they cannot infer removals.

Identifier intervals use half-open `[valid_from, valid_to)` semantics. If a previously closed provider code reappears, it creates/resolves a new canonical instrument. When no authoritative new list date is available, the prior `valid_to` is the conservative lower bound for the reused identifier, preventing overlap until a better lifecycle source is available.

## Decision 2 — One strict temporal resolver semantics

All temporal identifier consumers must follow the same rules:

- zero active matches: unresolved;
- exactly one active match: resolved;
- more than one active match: data-corruption error.

Classification uses a batch resolver with those semantics rather than its earlier last-row-wins map. Current-universe queries return only open primary identifiers (`valid_to IS NULL`).

## Decision 3 — Derived dirtiness is about canonical content, not provenance

Ingestion lineage answers *who/when wrote this row*. It does not answer whether canonical input content changed.

Adjustment input signatures therefore exclude `ingest_run_id`, `ingested_at`, and generated sequence IDs. The current `content-v1` signature is derived from stable canonical input content:

- daily: ordered trade date + OHLC + volume + amount + breadth values;
- corporate actions: ordered stable `source_record_id` values, with raw-field fallback for legacy rows.

This means:

- replaying the same inclusive daily boundary under a new ingest run remains clean;
- deleting/reinserting an identical full GBBQ snapshot remains clean;
- correcting an existing historical/boundary bar dirties adjustments;
- correcting GBBQ raw content dirties adjustments.

`market.adjustment_segment` and `meta.derived_state` continue to publish atomically, so a failed recalculation cannot mark stale output clean.

## Decision 4 — Daily quarantine evidence is one transaction

For one instrument/run, canonical valid bars, persisted validation violations, and the durable quarantine retry checkpoint are one atomic DuckDB transaction. Either all three effects commit or all roll back.

This preserves the invariant that a quarantined row cannot be forgotten because good rows committed while retry state failed to persist.

## Consequences

- Code reuse protection is effective in normal observed TDX workflows rather than existing only as store APIs.
- A missed entire absence interval still cannot be reconstructed from a current-only source; an authoritative security-lifecycle source remains desirable for exact historical dates.
- Adjustment dirty checking now works after the normal `sync-daily-all -> sync-actions -> calc-adjustments` sequence.
- Content signatures currently require SQL aggregation over an instrument's canonical history. If that becomes material at scale, content revision/signature maintenance should move into the merge/snapshot transaction without changing the semantic contract above.
