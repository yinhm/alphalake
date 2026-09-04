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
| Versioned schema migrations | Implemented | Embedded migrations are the single SQL source of truth; only unapplied versions run, each in its own transaction. |
| `meta.ingest_run` lifecycle | Implemented | Daily, corporate-action, classification and adjustment jobs create durable runs with terminal status. |
| Validation persistence | Implemented | Daily structural violations are stored in `meta.validation_result`. |
| Checkpoints | Partial | Durable checkpoints are currently used for daily-bar quarantine retry; generic checkpoint semantics are not yet shared by every dataset. |
| Raw artifact catalogue | Schema only | `meta.artifact` exists. Stable-file/document acquisition and immutable local retention are not implemented yet. |
| Broad source adapter interface | Removed | AlphaLake uses narrow consumer-defined interfaces per ingest workflow instead of forcing file/artifact semantics onto every source. |

## Reference / identity

| Capability | Status | Notes |
| --- | --- | --- |
| Canonical `instrument_id` | Implemented | TDX provider identifiers resolve to canonical instruments. |
| Provider identifier validity intervals | Partial | Columns exist, but code reuse / delist-and-reuse resolution is not yet implemented end-to-end. |
| Exchange master | Schema only | `ref.exchange` exists without a populated acquisition path. |
| Company master | Schema only | `ref.company` exists without a populated acquisition path. |
| Trading calendar | Schema only | Table exists; no authoritative calendar ingestion path is complete. |

## Market data

| Capability | Status | Notes |
| --- | --- | --- |
| TDX equity/ETF daily OHLCV | Implemented | Full bootstrap plus inclusive per-instrument incremental refresh. |
| Canonical daily date semantics | Implemented | Adapter emits date-only UTC-midnight carriers based on provider Y/M/D, independent of host timezone. |
| Canonical stock/ETF volume | Implemented | TDX hands are normalized to shares/units. |
| Daily structural validation | Implemented | Bad rows are quarantined while good rows continue; earliest bad date is retried via durable checkpoint. |
| Daily bulk persistence | Implemented | Per-instrument transaction uses DuckDB temporary staging + Appender + set-based upsert. |
| TDX GBBQ corporate actions | Implemented | Raw categories/fields retained; per-symbol snapshots replace atomically. |
| GBBQ snapshot safety | Implemented | Empty or suspiciously truncated snapshots do not erase the last known-good history by default. |
| Share-capital history | Implemented | Source-record identity supports multiple same-day/same-category records after migration 007. |
| QFQ/HFQ affine adjustments | Implemented | Derived from raw OHLCV + verified corporate-action semantics; cash distributions preserve additive terms. |
| Incremental derived recalculation | Planned | Adjustment calculation still needs dirty-input/signature based skipping. |

## Classification

| Capability | Status | Notes |
| --- | --- | --- |
| Canonical taxonomy/node model | Implemented | Provider taxonomy identity is separated from canonical DB IDs. |
| Temporal membership | Implemented | Half-open `[effective_from, effective_to)` history with same-day correction semantics. |
| TDX concept/style-region/index-block snapshots | Implemented | Complete family snapshots are diffed independently; failed/incomplete families cannot close old memberships. |
| TDX / Shenwan industry hierarchy | Partial | Source semantics/parser work exists or is being completed separately; treat the full ingest path as incomplete until tests and CLI integration are green. |
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

1. Complete temporal provider-identifier/code-reuse semantics before relying on historical identity across delist/relist events.
2. Add dirty-input signatures so adjustment calculations skip unchanged instruments.
3. Refactor repeated ingest-run/progress/error lifecycle only after the dataset-specific correctness semantics remain explicit.
4. Replace the placeholder `status` command with database-backed operational status.
5. Implement immutable artifact retention first for stable-file/document sources such as TDX professional financial packages and CNINFO filings.
6. Revisit provisional `fundamental` schema before its first production writer is introduced.
