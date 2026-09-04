# AlphaLake implementation status

This document records **what exists in the repository today**. It complements `design.md`, which describes the accepted target architecture and design contracts.

Status meanings:

- **Implemented** — executable production path exists and is covered by tests.
- **Partial** — some executable behavior exists, but the accepted design is not complete.
- **Schema only** — a table/model exists but no complete acquisition/processing path uses it yet.
- **Planned** — accepted direction only; no implementation should be inferred.

## Foundation

| Capability | Status | Notes |
| --- | --- | --- |
| DuckDB canonical store | Implemented | Single analytical backend; persistent files use stable `alphalake` catalog alias. |
| Versioned schema migrations | Implemented | Embedded migrations are the single SQL source of truth; only unapplied versions run, each in its own transaction and recorded after success. Legacy replay-only databases are upgraded by version registration. |
| Schema single source of truth | Implemented | Only `internal/store/duckdb/migrations/*.sql` is authoritative; the duplicate top-level `schema/` copies were removed. |
| `meta.ingest_run` lifecycle | Implemented | Daily, corporate-action, classification, industry and adjustment jobs create durable runs with terminal status; cancel-safe finalization/error joining is shared by a minimal ingest helper. |
| Operational status | Implemented | `alphalake status <db-path>` reads schema version/pending migrations, validation failures, checkpoints and recent ingest runs without mutating the database. |
| Validation persistence | Implemented | Daily structural violations are stored in `meta.validation_result`; good bars, validation evidence and quarantine retry state publish atomically per instrument/run. The obsolete standalone validation writer was removed so production daily validation has one transaction semantic. |
| Checkpoints | Partial | Durable checkpoints are used for daily-bar quarantine retry and security-master missing-identifier confirmation; generic checkpoint semantics are not yet shared by every dataset. |
| Raw artifact catalogue | Schema only | `meta.artifact` exists. Stable-file/document acquisition and immutable local retention are not implemented yet. |
| Broad source adapter interface | Removed | AlphaLake uses narrow consumer-defined interfaces per ingest workflow instead of forcing file/artifact semantics onto every source. |

## Reference / identity

| Capability | Status | Notes |
| --- | --- | --- |
| Canonical `instrument_id` | Implemented | TDX provider identifiers resolve to canonical instruments. |
| Provider identifier interval semantics | Implemented | Store uses half-open `[valid_from, valid_to)` intervals, explicit as-of resolution, ambiguity rejection and non-overlapping identity on code reuse. Classification uses the same strict temporal resolver semantics. |
| Observed TDX security-master lifecycle | Implemented | SH/SZ/BJ are independently verifiable/applicable partitions. A failed or suspicious partition is frozen without blocking healthy exchanges. Missing identifiers require absence in two distinct complete observations before close; a return before confirmation clears pending evidence. |
| Security-master partition isolation | Implemented | Source partition failure and store-side truncation/validation failure are isolated per exchange; downstream ingestion receives only successfully-applied partition observations. |
| Authoritative identifier lifecycle acquisition | Partial | Current-only TDX snapshots can observe disappearance/reappearance but cannot reconstruct an absence interval AlphaLake never observed or guarantee official delist/relist/code-change dates. When a closed code reappears without a list date, the previous `valid_to` is a conservative non-overlap lower bound until an authoritative source tightens it. |
| Exchange master | Schema only | `ref.exchange` exists without a populated acquisition path. |
| Company master | Schema only | `ref.company` exists without a populated acquisition path. |
| Trading calendar | Schema only | Table exists; no authoritative calendar ingestion path is complete. |

## Market data

| Capability | Status | Notes |
| --- | --- | --- |
| TDX equity/ETF daily OHLCV | Implemented | Full bootstrap plus inclusive per-instrument incremental refresh. Single-symbol and all-market commands share run/lineage/incremental semantics. |
| Canonical daily date semantics | Implemented | Adapter emits date-only UTC-midnight carriers based on provider Y/M/D, independent of host timezone. |
| Canonical stock/ETF volume | Implemented | TDX hands are normalized to shares/units. |
| Daily structural validation | Implemented | Bad rows are quarantined while good rows continue; earliest bad date is retried via durable checkpoint. Bars, validation evidence and checkpoint change are one transaction. |
| Daily bulk persistence | Implemented | Per-instrument transaction uses DuckDB temporary staging + Appender + set-based upsert rather than row-by-row OLTP writes. |
| TDX GBBQ corporate actions | Implemented | Raw categories/fields retained; per-symbol snapshots replace atomically. |
| GBBQ snapshot safety | Implemented | Empty or suspiciously truncated snapshots do not erase last-good history by default. `sync-actions <db> --force` is an explicit repair escape hatch for a successfully fetched snapshot; fetch/identity/database errors are never bypassed. |
| Corporate-action source identity | Implemented | Source IDs include raw-field bit fingerprints, allowing multiple same-day/same-category events. |
| Share-capital history | Implemented | Migration 007 includes `source_record_id` in identity, allowing multiple same-day/same-category records. |
| QFQ/HFQ affine adjustments | Implemented | Derived from raw OHLCV + verified corporate-action semantics; cash distributions preserve additive terms. |
| Incremental derived recalculation | Implemented | Migration 008 stores per-instrument input state. Dirtiness is based on canonical daily/action content rather than ingest lineage, so identical normal sync replay remains clean while same-date historical corrections dirty the output. Real `sync -> sync -> calc` workflow tests cover both cases. Derived segments and state publish atomically. |

## Classification

| Capability | Status | Notes |
| --- | --- | --- |
| Canonical taxonomy/node model | Implemented | Provider taxonomy identity is separated from canonical DB IDs. |
| Temporal membership | Implemented | Half-open `[effective_from, effective_to)` history with same-day correction semantics. |
| Strict temporal member resolution | Implemented | Batch resolution uses the same 0=unresolved, 1=resolved, >1=corruption semantics as canonical instrument resolution; overlapping identifiers cannot silently last-row-win. |
| TDX concept/style-region/index-block snapshots | Implemented | Complete family snapshots are diffed independently; failed/incomplete families cannot close old memberships. |
| TDX / Shenwan industry hierarchy | Implemented | Shared assignments/`incon.dat` are fetched once, then TDX and Shenwan taxonomies are built and applied independently. One taxonomy build failure yields a partial run while the other successful taxonomy still updates. |
| Historical memberships before AlphaLake observations | Planned | Prospective snapshot history is supported; pre-bootstrap history requires another authoritative historical source. |

## Fundamentals and authoritative validation

| Capability | Status | Notes |
| --- | --- | --- |
| `fundamental.*` tables | Schema only | Current schema is provisional and must not be treated as a finalized financial precision/model contract. |
| TDX professional financial (`gpcw`) ingestion | Planned | Accepted primary structured financial source; download/parser pipeline not complete. |
| Financial precision model | Planned | Runtime representation and DuckDB decimal/scaled-integer policy must be settled before canonical financial facts are implemented. |
| Point-in-time report/announcement semantics | Planned | Accepted contract, not yet backed by a complete ingestion path. |
| CNINFO filing catalogue/documents | Planned | Intended authoritative evidence/validation source; no production adapter yet. |
| CNINFO fact/date validation | Planned | No production validation path yet. |

## Later domains

| Capability | Status | Notes |
| --- | --- | --- |
| Fund master/NAV/holdings | Planned | No canonical schema/ingest slice completed. |
| Index master/weighted constituents | Planned | Generic temporal classification exists, but dedicated index domain is not implemented. |
| Derived research/factor datasets | Planned | Outside current ingestion-foundation work. |

## Known structural work still open

1. Add an authoritative security-lifecycle source/process so observed/conservative provider identifier boundaries can be tightened to official dates and missed absence intervals can be reconstructed.
2. Implement immutable artifact retention first for stable-file/document sources such as TDX professional financial packages and CNINFO filings.
3. Revisit the provisional `fundamental` schema and financial precision model before its first production writer is introduced.
4. If adjustment content-signature scans become material at scale, maintain content revision/signature incrementally inside canonical merge/snapshot transactions without changing the content-based dirty semantics.
