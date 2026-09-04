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
| `meta.ingest_run` lifecycle | Implemented | Daily, corporate-action, classification and adjustment jobs create durable runs with terminal status; cancel-safe finalization/error joining is shared by a minimal ingest helper. |
| Operational status | Implemented | `alphalake status <db-path>` reads schema version/pending migrations, validation failures, checkpoints and recent ingest runs without mutating the database. |
| Validation persistence | Implemented | Daily structural violations are stored in `meta.validation_result`. |
| Checkpoints | Partial | Durable checkpoints are currently used for daily-bar quarantine retry; generic checkpoint semantics are not yet shared by every dataset. |
| Raw artifact catalogue | Schema only | `meta.artifact` exists. Stable-file/document acquisition and immutable local retention are not implemented yet. |
| Broad source adapter interface | Removed | AlphaLake uses narrow consumer-defined interfaces per ingest workflow instead of forcing file/artifact semantics onto every source. |

## Reference / identity

| Capability | Status | Notes |
| --- | --- | --- |
| Canonical `instrument_id` | Implemented | TDX provider identifiers resolve to canonical instruments. |
| Provider identifier interval semantics | Implemented | Store uses half-open `[valid_from, valid_to)` intervals, explicit as-of resolution, open-identifier closure, ambiguity rejection and non-overlapping identity on code reuse. |
| Authoritative identifier lifecycle acquisition | Partial | Current TDX security-master snapshots do not by themselves provide all authoritative delist/relist/code-change dates. When a closed code reappears without a list date, AlphaLake uses the previous `valid_to` as a conservative non-overlap lower bound until a better lifecycle source tightens it. |
| Exchange master | Schema only | `ref.exchange` exists without a populated acquisition path. |
| Company master | Schema only | `ref.company` exists without a populated acquisition path. |
| Trading calendar | Schema only | Table exists; no authoritative calendar ingestion path is complete. |

## Market data

| Capability | Status | Notes |
| --- | --- | --- |
| TDX equity/ETF daily OHLCV | Implemented | Full bootstrap plus inclusive per-instrument incremental refresh. Single-symbol and all-market commands share run/lineage/incremental semantics. |
| Canonical daily date semantics | Implemented | Adapter emits date-only UTC-midnight carriers based on provider Y/M/D, independent of host timezone. |
| Canonical stock/ETF volume | Implemented | TDX hands are normalized to shares/units. |
| Daily structural validation | Implemented | Bad rows are quarantined while good rows continue; earliest bad date is retried via durable checkpoint. |
| Daily bulk persistence | Implemented | Per-instrument transaction uses DuckDB temporary staging + Appender + set-based upsert rather than row-by-row OLTP writes. |
| TDX GBBQ corporate actions | Implemented | Raw categories/fields retained; per-symbol snapshots replace atomically. |
| GBBQ snapshot safety | Implemented | Empty or suspiciously truncated snapshots do not erase the last known-good history by default. |
| Corporate-action source identity | Implemented | Source IDs include raw-field bit fingerprints, allowing multiple same-day/same-category events. |
| Share-capital history | Implemented | Migration 007 includes `source_record_id` in identity, allowing multiple same-day/same-category records. |
| QFQ/HFQ affine adjustments | Implemented | Derived from raw OHLCV + verified corporate-action semantics; cash distributions preserve additive terms. |
| Incremental derived recalculation | Implemented | Migration 008 records per-instrument input signatures; unchanged adjustment inputs skip historical loading/recalculation, while historical daily/action revisions dirty the output. Derived segments and state publish atomically. |

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

1. Add an authoritative security-lifecycle source/process so provider identifier `valid_from/valid_to` can be tightened from observed/conservative boundaries to official dates.
2. Implement immutable artifact retention first for stable-file/document sources such as TDX professional financial packages and CNINFO filings.
3. Revisit the provisional `fundamental` schema and financial precision model before its first production writer is introduced.
4. Complete and integrate TDX/Shenwan industry hierarchy ingestion, then resume the professional-financial slice.
