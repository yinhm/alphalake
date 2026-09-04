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
| `meta.ingest_run` lifecycle | Implemented | Daily, corporate-action, classification, industry, professional-financial and adjustment jobs create durable runs with terminal status; cancel-safe finalization/error joining is shared by a minimal ingest helper. |
| Operational status | Implemented | `alphalake status <db-path>` reads schema version/pending migrations, validation failures, checkpoints and recent ingest runs without mutating the database. |
| Validation persistence | Implemented | Daily structural violations are stored in `meta.validation_result`; good bars, validation evidence and quarantine retry state publish atomically per instrument/run. Acquisition/workflow diagnostics such as security-master partition failures are also persisted as queryable run evidence. The obsolete standalone daily validation writer was removed so production daily validation has one transaction semantic. |
| Checkpoints | Partial | Durable checkpoints are used for daily-bar quarantine retry, security-master missing-identifier confirmation, and completed professional-financial package revisions; generic checkpoint semantics are not yet shared by every dataset. |
| Immutable raw artifact store | Implemented | Common SHA-256 content-addressed store writes raw bytes atomically, verifies retained bytes on reload, records `meta.artifact` lineage, reuses historical locator revisions, and can recover corrupt/missing retained financial package bytes by re-downloading through provider integrity verification and atomically repairing the content-addressed object. |
| Broad source adapter interface | Removed | AlphaLake uses narrow consumer-defined interfaces per ingest workflow instead of forcing file/artifact semantics onto every source. |

## Reference / identity

| Capability | Status | Notes |
| --- | --- | --- |
| Canonical `instrument_id` | Implemented | TDX provider identifiers resolve to canonical instruments. |
| Provider identifier interval semantics | Implemented | Store uses half-open `[valid_from, valid_to)` intervals, explicit as-of resolution, ambiguity rejection and non-overlapping identity on code reuse. Classification and professional-financial ingestion use strict temporal resolver semantics. |
| Observed TDX security-master lifecycle | Implemented | SH/SZ/BJ are independently verifiable/applicable partitions. A failed or suspicious partition is frozen without blocking healthy exchanges. Missing identifiers require absence in two distinct complete observations before close; a return before confirmation clears pending evidence. Flat/partition structure is preflight-validated before any partition transaction begins. |
| Security-master partition isolation | Implemented | Source partition failure and store-side truncation/validation failure are isolated per exchange; downstream ingestion receives only successfully-applied partition observations. Failed partitions are returned in ingest summaries, persisted in `meta.validation_result`, and force otherwise healthy runs to terminal `partial` rather than silently `completed`. |
| Legacy security-master safety | Implemented | Compatibility snapshots without explicit partitions retain one transaction, repeated-absence semantics, and a global size/truncation guard rather than silently weakening destructive-safety checks. |
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
| TDX / Shenwan industry hierarchy | Implemented | Shared assignments/`incon.dat` are fetched once, then TDX and Shenwan taxonomies are built and applied independently. One taxonomy build failure yields a partial run while the other successful taxonomy still updates. Historical `incon.dat` variants may omit unnamed intermediate levels; AlphaLake skips those levels but never invents a membership leaf. |
| Historical memberships before AlphaLake observations | Planned | Prospective snapshot history is supported; pre-bootstrap history requires another authoritative historical source. |

## Fundamentals and authoritative validation

| Capability | Status | Notes |
| --- | --- | --- |
| TDX professional financial raw acquisition | Implemented | `sync-financial <db>` fetches `gpcw.txt`, verifies listed package size/MD5, retains manifest/package artifacts, and defaults to the newest package; `--all` explicitly requests full history. Historical retained revisions are reused when the manifest rolls back; corrupt/missing retained revisions fall back to verified provider download and local repair. |
| Lossless `gpcw` parser | Implemented | Dynamic field count comes from `report_size/4`; exact float32 bits plus analytical float64 values are preserved. Package structure/offset validation is covered by tests. The raw one-byte market marker is retained but has no inferred exchange semantics yet. |
| Raw financial identity boundary | Implemented | Source normalization preserves provider code + raw market marker and does not use current SDK code-range classifiers to invent an exchange prefix. Tests cover B-share-style and legacy Beijing/NEEQ-style codes. |
| Financial temporal code resolution | Implemented | Raw six-digit codes are resolved by querying active TDX provider symbols at the report period. The gpcw candidate universe excludes indexes by dataset semantics, so `sh000001` does not make Ping An Bank `sz000001` ambiguous. Exactly one non-index candidate resolves; zero or multiple eligible candidates remain pending rather than guessed. Overlap of the same full provider identifier remains corruption. |
| Provider-record resolution evidence | Implemented | Migration 011 stores `resolved`, `pending`, or operator-`acknowledged` state per artifact revision/raw code. Pending records remain replayable; acknowledgement requires a reason; unresolved replay preserves the reviewed machine reason; later authoritative resolution supersedes acknowledgement and clears obsolete ack metadata. `financial-unresolved --limit/--offset`, `financial-ack`, and `financial-unack` expose the reversible governance path. |
| `fundamental.provider_fact` production writer | Implemented | Migration 012 separates immutable provider-record identity from canonical instrument identity. Provider facts are reconciled by artifact revision + raw provider code + FN field; later lifecycle corrections reassign existing facts or remove stale facts if a record becomes unresolved instead of leaving duplicate facts on multiple instruments. Writer/CLI report attempted, inserted, reassigned and removed counts. |
| Core TDX provider field catalog | Implemented | Migration 010 maps reviewed official fields FN230–FN238 to stable canonical names; unreviewed FN fields remain available losslessly as provider facts without speculative canonical mapping. |
| Financial package completion semantics | Implemented | A package checkpoint means every provider record is either resolved or explicitly acknowledged. Pending records prevent completion. Un-acknowledgement returns a record to pending and invalidates that package checkpoint transactionally so the next financial sync re-evaluates it. |
| Financial precision model | Partial | Provider raw precision is explicit: exact float32 bits + exactly representable float64 analytical value. Canonical `fundamental.fact` decimal/unit policy remains to be finalized before materialization. |
| Point-in-time report/announcement semantics | Partial | Report period and as-of identity resolution are implemented. `gpcw` package parsing does not provide an authoritative per-record announcement timestamp, so AlphaLake deliberately leaves provider `announcement_time` NULL rather than inferring it. |
| Canonical `fundamental.fact` materialization | Planned | Table exists, but no production writer is enabled until authoritative announcement time and canonical unit/precision semantics are available. |
| CNINFO filing catalogue/documents | Planned | Intended authoritative evidence/announcement-time/validation source; no production adapter yet. |
| CNINFO fact/date validation | Planned | No production validation path yet. |

## Later domains

| Capability | Status | Notes |
| --- | --- | --- |
| Fund master/NAV/holdings | Planned | No canonical schema/ingest slice completed. |
| Index master/weighted constituents | Planned | Generic temporal classification exists, but dedicated index domain is not implemented. |
| Derived research/factor datasets | Planned | Outside current ingestion-foundation work. |

## Known structural work still open

1. Add an authoritative security-lifecycle source/process so observed/conservative provider identifier boundaries can be tightened to official dates and missed absence intervals can be reconstructed.
2. Add authoritative financial filing/announcement metadata (CNINFO is the preferred next source) so provider facts can become true point-in-time canonical facts without invented timestamps.
3. Finalize canonical financial unit/decimal policy before the first `fundamental.fact` production writer; expand the curated FN mapping only with reviewed semantics.
4. Research and verify the raw gpcw market-marker byte against authoritative/provider evidence before assigning it exchange semantics; until then it remains raw evidence only.
5. If adjustment content-signature scans become material at scale, maintain content revision/signature incrementally inside canonical merge/snapshot transactions without changing the content-based dirty semantics.
