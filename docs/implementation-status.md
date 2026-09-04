# AlphaLake implementation status

This document records **what exists in the repository today**. It complements `design.md`, which describes the accepted target architecture and compatibility contracts.

Status meanings:

- **Implemented** — an executable production path exists and is covered by tests.
- **Partial** — executable behavior exists, but the accepted design is not complete.
- **Schema only** — a table/model exists without a complete acquisition or processing path.
- **Planned** — accepted direction only; no implementation should be inferred.

## Foundation

| Capability | Status | Notes |
| --- | --- | --- |
| DuckDB canonical store | Implemented | DuckDB is the single analytical backend; persistent databases use the stable `alphalake` catalog alias. |
| Versioned migrations | Implemented | Embedded migrations are the only SQL source of truth. Only pending versions run, each in its own transaction, and successful versions are recorded. Current schema includes migrations 001–017. |
| Ingest-run lifecycle | Implemented | Market, classification, financial, CNINFO filing, and canonical-materialization workflows create durable runs with `completed`, `partial`, `failed`, or `canceled` terminal state. |
| Operational status | Implemented | `alphalake status` reports schema version, pending migrations, validation failures, checkpoints, and recent runs without migrating the database. |
| Validation persistence | Implemented | OHLCV violations, acquisition diagnostics, invalid catalogue records, and rejected canonical financial candidates are queryable in `meta.validation_result`. |
| Checkpoints | Partial | Dataset-specific checkpoints support daily quarantine retry, security-master disappearance confirmation, TDX financial package completion, and completed CNINFO catalogue windows. There is intentionally no single global checkpoint meaning. |
| Immutable artifact store | Implemented | SHA-256 content addressing, root-relative paths, temporary-file/fsync/rename publication, retained-version reuse, load-time verification, and corrupt/missing-file recovery are implemented. TDX manifests/packages and CNINFO catalogue pages/documents use it. |
| Broad adapter interface | Removed | Workflows define narrow consumer interfaces instead of forcing one source shape onto protocol responses, paginated APIs, and stable files/documents. |

## Reference and identity

| Capability | Status | Notes |
| --- | --- | --- |
| Canonical `instrument_id` | Implemented | Provider symbols resolve to stable canonical instruments. |
| Temporal provider identifiers | Implemented | Half-open `[valid_from, valid_to)` intervals, explicit as-of resolution, overlap rejection, and non-overlapping code reuse are implemented. |
| TDX security-master lifecycle | Implemented | SH/SZ/BJ are independently validated/applied. Failed or suspicious partitions freeze without blocking healthy exchanges. Missing identifiers require two distinct complete observations before close. |
| Security-master diagnostics | Implemented | Partition failures are returned in summaries, persisted as validation evidence, and force otherwise healthy runs to `partial`. |
| Legacy master safety | Implemented | Non-partitioned compatibility snapshots retain repeated-absence semantics and global truncation protection. |
| Authoritative historical lifecycle | Partial | Current TDX snapshots can observe disappearance/reappearance after AlphaLake begins observing. They cannot reconstruct missed historical absence intervals or guarantee official list/delist/relist/code-change dates. |
| Exchange master | Schema only | `ref.exchange` exists without an authoritative population workflow. |
| Company master | Schema only | `ref.company` exists without a production acquisition path. |
| Trading calendar | Schema only | The table exists; no authoritative exchange-calendar workflow is complete. |

## Market data

| Capability | Status | Notes |
| --- | --- | --- |
| TDX equity/ETF daily OHLCV | Implemented | Full bootstrap plus inclusive per-instrument incremental refresh. Single-symbol and all-market paths share run, lineage, validation, and retry semantics. |
| Canonical daily dates | Implemented | Provider Y/M/D is normalized to a date-only UTC carrier independent of the host timezone. |
| Canonical volume | Implemented | Stock/ETF TDX hands are converted to shares or units. |
| Daily validation/quarantine | Implemented | Invalid rows are quarantined while good rows continue. Earliest bad date remains retryable. Good rows, validation evidence, and retry checkpoint publish in one transaction. |
| Daily bulk persistence | Implemented | Per-instrument DuckDB Appender/staging and set-based upsert replace row-by-row OLTP writes. |
| TDX GBBQ corporate actions | Implemented | Raw categories and C1–C4 fields are retained; per-symbol snapshots replace atomically. |
| GBBQ last-known-good safety | Implemented | Empty/suspicious truncation is refused by default. `--force` bypasses only the snapshot-size guard after successful acquisition. |
| Share-capital history | Implemented | Raw source-record identity permits multiple same-day/same-category observations. |
| QFQ/HFQ affine adjustments | Implemented | Derived locally from unadjusted prices and verified corporate-action semantics, including cash-distribution additive terms. |
| Incremental adjustment derivation | Implemented | Dirtiness is based on canonical content, not ingest lineage. Identical source replay remains clean; same-date historical correction dirties output. Derived segments and state publish atomically. |

## Classification

| Capability | Status | Notes |
| --- | --- | --- |
| Taxonomy/node model | Implemented | Provider taxonomy identity is separate from canonical database identity. |
| Temporal membership | Implemented | Half-open effective intervals and same-day correction behavior are supported. |
| Strict member resolution | Implemented | Batch resolution uses 0=unresolved, 1=resolved, >1=corruption semantics; overlapping identifiers cannot silently last-row-win. |
| TDX concept/style-region/index-block | Implemented | Families update independently; failed/incomplete families cannot close prior membership. |
| TDX and Shenwan industry hierarchy | Implemented | Shared source inputs are acquired once, each taxonomy is built/applied independently, and sparse unnamed intermediate levels are tolerated without inventing membership leaves. |
| Pre-bootstrap membership history | Planned | Prospective snapshot history exists; historical memberships before AlphaLake observation require another source. |

## TDX professional financial provider layer

| Capability | Status | Notes |
| --- | --- | --- |
| Manifest/package acquisition | Implemented | `sync-financial` fetches `gpcw.txt`, verifies package size/MD5, retains manifest/package artifacts, defaults to the newest package, and supports explicit `--all` backfill. |
| Lossless gpcw parser | Implemented | Field count derives from `report_size/4`; package offsets/structure are validated; original float32 bits plus analytical float64 values are retained. |
| Raw financial identity | Implemented | The adapter retains six-digit provider code and raw marker without assigning unverified market semantics or using present-day code-range classification. |
| Temporal raw-code resolution | Implemented | Candidate symbols are queried at the report period. Indexes are excluded by dataset semantics; one eligible candidate resolves, zero/multiple remain pending, and overlapping full identifiers are corruption. |
| Resolution governance | Implemented | `resolved`, `pending`, and operator-`acknowledged` evidence is durable per artifact/code. Ack is reversible, pending rows are pageable, and later authoritative resolution supersedes acknowledgement. |
| Provider-fact writer | Implemented | Every FN field is bulk persisted with artifact/run lineage and exact provider bits. Immutable raw identity is separate from canonical instrument identity, so later identity correction reassigns facts instead of duplicating them. |
| Provider artifact recovery | Implemented | Historical retained revisions are reused; corrupt/missing local objects fall back to provider download, integrity verification, and atomic repair. |
| Reviewed provider mapping | Partial | FN230–FN238 are reviewed and mapped. Other FN fields remain lossless provider evidence but are not canonicalized until their semantics and units are reviewed. |

## CNINFO filing evidence

| Capability | Status | Notes |
| --- | --- | --- |
| CNINFO catalogue client | Implemented | Paginated `/new/hisAnnouncement/query` acquisition includes retry/backoff, rate limiting, response-size limits, bounded windows, and row-level parsing failures. |
| Catalogue artifacts | Implemented | Every acquired page is retained before normalized filing metadata is written. Artifact locator records date window, page, and page size. |
| Periodic-report classification | Implemented | Q1/H1/Q3/annual reports, summaries, correction notices, corrected reports, and revisions are distinguished. Postponement notices, inquiry letters, presentations, forecasts, flashes, and similar references cannot anchor PIT facts. |
| Filing identity/model | Implemented | `fundamental.filing` is unique by source filing ID and retains provider code, exchange evidence, report period, classifier/raw metadata, first/last seen, resolution, and artifact lineage. |
| Filing document evidence | Implemented | Eligible full/correction/revision documents are downloaded by default, semantically checked against empty/HTML/non-PDF responses, and retained as immutable artifacts. `filing_document` retains revision history. `--metadata-only` is explicit. |
| Filing identity resolution | Implemented | Explicit exchange evidence resolves exact temporal TDX equity identifiers at the disclosure date; missing exchange evidence uses strict equity-only raw-code resolution. Unsupported/ambiguous/missing identity remains pending. |
| Pending filing recovery | Implemented | `materialize-fundamentals` re-resolves retained pending filings locally after lifecycle enrichment; no catalogue/document redownload is required. `filing-unresolved` exposes pageable pending evidence. |
| Correction predecessor relation | Implemented | A correction/revision can link to the immediately preceding eligible report anchor while preserving its own source identity. |
| Catalogue-window resumability | Implemented | Old complete windows may be checkpoint-skipped; recent windows are rescanned so late corrections/revisions remain discoverable; `--rescan` overrides old-window checkpoints. Any acquisition/artifact/diagnostic/write failure withholds the window checkpoint. |
| Disclosure time precision | Implemented | The public catalogue is treated as date precision. China-local disclosure date and raw provider milliseconds are retained; canonical PIT availability is the next China-calendar-day boundary, with precision recorded as `date`. No intraday timestamp is invented. |

## Canonical point-in-time fundamentals

| Capability | Status | Notes |
| --- | --- | --- |
| Provider-to-filing link | Implemented | `fundamental.provider_filing_link` keeps TDX numerical provenance separate from CNINFO filing provenance. Candidate filing must match instrument, report period, report type, and be available no later than provider-artifact observation time. |
| Correction-aware linking | Implemented | Earlier observed provider revisions link only to filings already available then; later revisions can link to later corrections. Equally ranked candidates remain ambiguous. |
| Canonical precision/unit policy | Implemented for reviewed fields | FN230–FN237 normalize to CNY yuan; FN238 to shares. Values use `DECIMAL(38,10)` as deterministic representation of provider float32, not as recovery of unavailable source precision. Statement scope remains `provider_default`. |
| PIT fact materializer | Implemented | `materialize-fundamentals` is local-only. It retries pending filing identities, refreshes links, validates candidates, and reconciles canonical facts by immutable raw identity. |
| Canonical reconciliation | Implemented | Identity/mapping/filing corrections update the existing raw-identity fact. Unsupported or newly unsafe evidence removes stale canonical facts. Rejected candidates create validation evidence. |
| Latest view | Implemented | `fundamental.fact_latest` selects the latest available revision per instrument/canonical field/report period. |
| ASOF macro | Implemented | `fundamental.fact_asof(as_of_time)` selects the latest revision whose authoritative availability is no later than the supplied timestamp. Tests prove no row before original disclosure and corrected value only after correction availability. |
| End-to-end evidence chain | Implemented | Tests cover CNINFO catalogue HTTP parsing, document retention, TDX provider revision persistence, explicit link, canonical materialization, original/corrected revisions, and ASOF behavior. |
| CNINFO numerical fact extraction/validation | Planned | Original documents are retained, but AlphaLake does not yet extract PDF/XBRL values or numerically compare selected official values with TDX provider facts. |

## Later domains

| Capability | Status | Notes |
| --- | --- | --- |
| Dedicated index master/weights | Planned | Generic temporal classification exists, but a dedicated weighted-constituent domain is not complete. |
| Fund master/NAV/AUM/holdings | Planned | No complete canonical fund slice exists. |
| Intraday market data | Planned | TDX parser/source support exists upstream, but canonical intraday session/time/unit persistence is not implemented. |
| Derived research/valuation/factors | Planned | Outside the ingestion and PIT-fundamental foundation completed here. |

## Known work still open

1. Extract selected values from authoritative CNINFO documents or structured attachments and compare them with TDX provider facts.
2. Add an authoritative security-lifecycle source so official list/delist/relist/code-change dates and missed historical absence intervals can replace conservative observed boundaries.
3. Expand the reviewed FN catalogue and statement dimensions only after semantics, units, scope, and industry-specific fields are reviewed.
4. Add an authoritative trading-calendar workflow and populate exchange/company reference masters.
5. Verify the raw gpcw marker byte independently before assigning it market semantics; until then it remains raw evidence only.
6. If adjustment content-signature scans become material at scale, maintain signatures incrementally without changing content-based correctness semantics.
7. Build dedicated index, fund, intraday, valuation, factor, screening, and backtest domains after the reference/fundamental foundation stabilizes.
