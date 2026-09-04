# AlphaLake design

Status: accepted target architecture, v0

This document contains:

1. the accepted final specification for the current AlphaLake foundation;
2. the important design choices and the reasoning that led to it.

It is normative for architecture and compatibility direction. It is not, by itself, a claim that every future domain exists. Current executable status is recorded in [`implementation-status.md`](implementation-status.md), and detailed decisions are preserved in [`decisions/`](decisions/).

## 1. Product definition

AlphaLake is a local-first financial data lake and analytical store for reproducible investment research.

It is designed for:

- historical market and fundamental research;
- point-in-time queries without future information leakage;
- source evidence retention and deterministic rebuilds;
- multi-source validation;
- local SQL analysis through DuckDB.

It is not a trading execution system, a market-data redistribution service, or an abstraction over multiple analytical databases.

The first production scope uses:

- TDX as the primary structured source for A-share market data and professional financial values;
- CNINFO as the authoritative filing identity, disclosure-date, and original-document source;
- DuckDB as the canonical analytical store.

## 2. Architectural boundaries

```text
External sources
    |
    +-- TDX --------------------------------------+
    |                                             |
    +-- CNINFO -----------------------------------+----> narrow source adapters
    |                                                        |
    +-- future providers ------------------------------------+
                                                             |
                         +-----------------------------------+
                         |
                         +--> immutable raw artifacts, when a stable
                         |    source file/document naturally exists
                         |
                         +--> provider-neutral observations
                                  |
                                  +--> temporal identity resolution
                                  +--> validation / quarantine
                                  +--> canonical domain merge
                                            |
                                            +--> DuckDB
                                            +--> reproducible local derivations
```

Source adapters own provider transport and decoding. Provider SDK types must not escape adapter packages.

AlphaLake deliberately does not define one broad adapter interface. A paginated HTTP catalogue, a binary report file, a protocol response, and an original PDF are different source shapes. Ingest workflows define narrow consumer interfaces for the capabilities they need.

## 3. Canonical domains

DuckDB uses domain-oriented schemas:

- `meta` — schema versions, ingest runs, artifacts, checkpoints, validation results, derived state;
- `ref` — instruments, temporal identifiers, exchanges, companies, calendars;
- `market` — unadjusted OHLCV, corporate actions, share capital, adjustment segments;
- `classification` — taxonomies, nodes, temporal membership;
- `fundamental` — provider fields/facts, filings, provider-filing links, canonical PIT facts;
- future `fund`, `index`, and `derived` domains.

Temporary relations are used for bulk staging and set-based reconciliation. They are implementation details rather than durable source truth.

## 4. Canonical identity

Provider symbols are identifiers, not primary identities.

`ref.instrument.instrument_id` is the stable canonical identity. `ref.instrument_identifier` stores provider identifiers with half-open validity intervals:

```text
[valid_from, valid_to)
```

Required behavior:

- a provider symbol change must not create a new instrument merely because the string changed;
- code reuse by a different security must create a distinct instrument;
- overlapping active intervals for the same full provider identifier are corruption, not a tie to resolve arbitrarily;
- point-in-time joins resolve identifiers at the observation date relevant to the source record.

TDX current-universe snapshots are exchange-partitioned. Destructive close decisions require a complete partition and repeated absence evidence. Failed or suspicious partitions freeze without blocking healthy exchanges, and their failures remain queryable.

Current-only TDX observations cannot reconstruct an absence interval that AlphaLake never observed. Official historical listing, delisting, relisting, transfer, and code-change dates remain the responsibility of a future authoritative lifecycle source.

## 5. Raw artifacts and lineage

A stable source file or document is immutable evidence.

Every retained artifact records at least:

- source and dataset;
- source locator;
- fetch time;
- SHA-256;
- content length;
- local root-relative path;
- parser/ingest version when applicable;
- ingest-run lineage.

Artifact storage requirements:

- content-addressed physical paths;
- temporary-file write, fsync, and atomic rename;
- hash verification on reuse;
- same bytes may share one physical object while retaining separate locator lineage rows;
- corrupt or missing cache objects may be recovered only through independently verified provider acquisition;
- a newer parser must be able to rebuild canonical data from retained bytes without redownloading.

Protocol responses without a natural stable artifact are not artificially wrapped as files. They still retain run, source-record, and validation lineage.

## 6. Market-data semantics

`market.ohlcv_daily` stores unadjusted prices only.

Canonical semantics:

- daily observations are exchange/provider calendar dates represented as date-only values, independent of host timezone;
- stock/ETF volume is stored in shares or units, not TDX hands;
- ingestion time is timezone-aware;
- adjusted prices are reproducible derivatives, not primary truth.

Invalid daily rows are quarantined individually. Good rows continue, while the earliest invalid historical date remains eligible for re-fetch. Valid rows, validation evidence, and retry checkpoint state publish atomically per instrument.

Bulk writes use DuckDB Appender/staging plus set-based merge, while preserving useful per-instrument recovery boundaries.

## 7. Corporate actions and adjustments

TDX GBBQ decoding may use the TDX SDK, but AlphaLake owns canonical interpretation.

Every corporate-action observation preserves:

- raw category;
- raw provider fields;
- strong source-record identity;
- normalized semantics only where the interpretation is verified.

Successful-but-empty or suspiciously truncated full snapshots must not erase the last known-good history by default. An explicit operator repair may bypass the snapshot-size guard, but never acquisition, identity, or database errors.

QFQ/HFQ adjustment segments are local affine derivations from raw OHLCV and verified corporate-action semantics. Cash distributions retain the additive component rather than being forced into a purely multiplicative model.

Derived-data dirtiness is based on canonical content, not ingest-run timestamps or surrogate IDs.

## 8. Classification semantics

Classification membership is temporal.

A membership records an effective interval rather than only current state. Complete source snapshots are diffed prospectively to produce history without look-ahead.

Failed/incomplete families or taxonomies cannot close previous membership. Unresolved members make the affected snapshot incomplete rather than silently disappearing from history.

This model supports TDX concept/style/region/index blocks and TDX/Shenwan industries. Historical membership before AlphaLake begins observing requires an additional historical source.

## 9. TDX professional financial provider layer

TDX professional financial data is acquired from `tdxfin/gpcw.txt` and `gpcwYYYYMMDD.zip`.

The parser must:

- derive field count from `report_size / 4`;
- validate header, offsets, record boundaries, and archive structure;
- preserve every provider field as `FN1...FNn`;
- preserve the exact float32 bit pattern and an analytical float64 value;
- retain the raw six-digit provider code and raw one-byte marker;
- never infer historical exchange identity from present-day code-range helpers.

`github.com/injoyai/tdx` supplies report-file transport but currently lacks a lossless gpcw codec. AlphaLake therefore owns a deliberately narrow codec, with upstream contribution or a minimal fork preferred if the implementation stabilizes and fits upstream scope.

Provider-fact revision identity is the immutable artifact revision. Raw provider-record identity is separate from canonical instrument identity, allowing later lifecycle correction to reassign or remove canonical links without producing duplicate facts.

A provider package is complete only when every raw record is either resolved or explicitly operator-acknowledged. Pending evidence remains locally replayable.

## 10. CNINFO filing evidence

CNINFO is the authoritative disclosure-evidence source, not the primary structured numerical provider.

### 10.1 Catalogue acquisition

The filing catalogue is acquired through bounded date windows and pages. Every page is persisted as an immutable artifact before normalized metadata is written.

Old completed windows may be checkpoint-skipped. Recent windows are rescanned so late corrections, revisions, and metadata changes remain discoverable. A source/artifact/diagnostic/write failure withholds the window checkpoint.

### 10.2 Filing identity

`fundamental.filing` is unique by:

```text
(source, source_filing_id)
```

An instrument/report-period pair is not filing identity because one period may have a full report, summary, correction notice, corrected report, revision, audit report, inquiry letter, or other related document.

Normalized filing metadata retains:

- provider code and exchange evidence;
- source filing ID;
- filing type and variant;
- report period;
- disclosure date and canonical availability time;
- original title/category/classifier version;
- original catalogue fields;
- first/last observation times;
- catalogue and document artifact lineage;
- resolution status and reason.

### 10.3 Conservative filing classification

Only explicit Q1, semiannual, Q3, and annual report wording can produce periodic-report semantics.

Summaries are retained but cannot anchor statement facts. Postponement/reservation notices, inquiry letters, presentations, board resolutions, forecasts, earnings flashes, and similar references are evidence but not PIT statement filings.

Correction notices, corrected reports, and revisions remain distinct source filings. A correction may point to the immediately preceding eligible report anchor without losing its own identity.

### 10.4 Filing instrument resolution

Explicit exchange evidence is used to resolve the exact temporal TDX equity identifier at the disclosure date. If exchange evidence is absent, a strict equity-only raw-code search is used.

Unknown non-empty exchange evidence is not discarded and does not silently fall back to another market. It remains pending. Missing and ambiguous identities also remain pending and are locally re-resolved after future lifecycle enrichment.

### 10.5 Filing documents

Eligible full/corrected/revision documents are downloaded by default and retained as immutable artifacts. Metadata-only mode is explicit.

Document acquisition rejects empty payloads, HTML/anti-bot responses, and expected PDF payloads without a PDF signature. Hash-valid but semantically invalid retained payloads are not reused.

## 11. Disclosure-time precision

Report period, disclosure availability, and ingestion time are separate concepts.

The public CNINFO catalogue establishes a China-local disclosure **date**, but its millisecond field is not promoted to a verified intraday publication timestamp.

For catalogue-derived filings AlphaLake stores:

- `announcement_date` — China-local disclosure date as a date value;
- `raw_announcement_time_ms` — unmodified provider evidence;
- `announcement_time_precision='date'`;
- `announcement_time` — the next China-calendar-day boundary, used as the earliest safe PIT availability instant.

This deliberately sacrifices same-day availability rather than leaking information into intraday historical queries. A future independently verified timestamp source may use precision `timestamp` without changing the query contract.

## 12. Provider-to-filing links

TDX values and CNINFO filing evidence never overwrite each other's provenance.

`fundamental.provider_filing_link` explicitly connects one immutable provider-record revision to one filing.

A candidate filing must have:

- the same canonical instrument;
- the same report period;
- a compatible periodic-report type;
- availability no later than the first observation time of the provider artifact.

The observation-time constraint prevents a later correction from leaking backward into an earlier observed provider revision.

Among eligible candidates, later availability wins. At the same availability time, corrected-report/revision/correction-notice/full-report priority is deterministic. Equally ranked candidates remain ambiguous.

## 13. Canonical point-in-time fundamentals

Only reviewed provider mappings with known units may become canonical facts.

The initial canonical set is:

- FN230 revenue — CNY yuan;
- FN231 operating profit — CNY yuan;
- FN232 parent net income — CNY yuan;
- FN233 adjusted net income — CNY yuan;
- FN234 operating cash flow — CNY yuan;
- FN235 investing cash flow — CNY yuan;
- FN236 financing cash flow — CNY yuan;
- FN237 net cash increase — CNY yuan;
- FN238 total shares — shares.

Provider precision remains lossless in `fundamental.provider_fact`. Canonical values use `DECIMAL(38,10)` as a deterministic decimal representation of the provider float32 value. This does not restore precision that the provider encoding did not contain.

Statement scope is `provider_default` until the provider record supplies a reviewed scope dimension. AlphaLake does not invent consolidated/parent scope.

`materialize-fundamentals` is a local-only reconciliation:

1. retry retained pending filing identities;
2. refresh provider-to-filing links;
3. validate linked provider facts;
4. insert/update/remove canonical facts by immutable raw identity;
5. persist rejected candidates as validation evidence.

A canonical fact is not materialized when identity, period, report type, announcement ordering, finiteness, unit, or decimal conversion is unsafe.

## 14. Point-in-time query contract

`fundamental.fact_latest` returns the latest supported revision for each instrument, canonical field, and report period.

`fundamental.fact_asof(as_of_time)` returns the latest revision whose canonical disclosure availability is no later than `as_of_time`.

Required behavior:

```text
before original disclosure availability -> no fact
after original disclosure availability  -> original supported revision
after correction availability           -> corrected supported revision
```

This behavior is bounded by observed provider revisions. If AlphaLake never retained a pre-correction TDX revision, it cannot invent the original value merely because CNINFO records an earlier filing.

## 15. Validation contract

Validation is queryable data, not only logs.

Initial market rules include OHLC ordering, non-negative volume, and canonical uniqueness.

Initial fundamental materialization rules include:

- filing instrument equals provider instrument;
- filing report period equals provider report period;
- filing type matches the period;
- disclosure availability is not before report period;
- provider value is finite;
- canonical mapping and unit are reviewed;
- decimal conversion succeeds;
- mapping intervals are unambiguous.

Original CNINFO documents are retained now. Numerical extraction from PDF/XBRL and selected official-value comparison with TDX remain the next validation layer.

## 16. Operational workflow

Normal refresh:

```text
sync-daily-all
sync-actions
calc-adjustments
sync-classifications
sync-industries
sync-financial
sync-filings
materialize-fundamentals
```

Historical bootstrap uses explicit `--all` for large TDX financial and CNINFO filing backfills.

Network ingestion and local derivation remain separate. Raw/provider truth can be refreshed independently; canonical derived data can be rebuilt without network access.

## 17. Non-goals for v0

- order execution or portfolio trading;
- commercial data redistribution;
- multiple analytical storage backends;
- EastMoney or Tushare as required dependencies;
- adjusted prices as primary facts;
- speculative canonicalization of unreviewed provider fields;
- fabricated announcement timestamps or statement scope.

# 18. Important design decisions and process

The architecture evolved through explicit review-driven decisions rather than a single fixed upfront schema.

### D-001 — DuckDB instead of Pebble as canonical storage

The workload requires relational point-in-time joins, window functions, ASOF analysis, columnar scans, and Parquet interoperability. A KV-only design would require AlphaLake to rebuild query and indexing semantics itself.

### D-002 — Source abstraction, not database abstraction

After choosing DuckDB, a multi-backend repository layer no longer created value. Source/provider boundaries, canonical semantics, and reproducible ingestion are the real variability points.

### D-003 — Reuse TDX transport/codecs, retain AlphaLake semantics

`injoyai/tdx` is used where practical for TDX protocol/file mechanics. AlphaLake owns units, temporal identity, validation, and canonical meaning. The gpcw codec is a documented narrow exception because upstream does not currently provide it.

### D-004 — Raw artifacts are evidence

Direct HTTP/protocol-to-database ingestion was rejected for stable files/documents. Immutable bytes permit parser correction, audit, historical revision comparison, and offline rebuild.

### D-005 — Temporal identity and destructive-change grace

Provider symbol strings cannot safely be canonical identities. Current-source omissions also cannot immediately close history. Partition isolation and repeated evidence were introduced after review exposed identity fragmentation and availability risks.

### D-006 — Content and lineage are different

Ingest-run IDs describe provenance; they do not prove content changed. Derived-data dirtiness therefore uses canonical content rather than timestamps/surrogate IDs.

### D-007 — Quarantine poison records, do not wedge datasets

A bad daily row or unresolved financial record must not permanently block later good data. Valid rows progress while bad evidence remains visible and retryable.

### D-008 — Do not invent point-in-time semantics

TDX gpcw report periods were available before authoritative announcement time. Canonical facts remained intentionally empty until CNINFO filing evidence existed. This prevented a convenient but invalid substitution of fetch time, filename, or report period for market availability.

### D-009 — Provider values and filing evidence remain independent

CNINFO timing is not written into TDX facts as though TDX supplied it. An explicit link relation preserves source roles and supports ambiguity/correction handling.

### D-010 — Date-only disclosure is conservative, not fake precision

The public catalogue's date evidence is represented as date precision and becomes PIT-visible at the next China-day boundary. Same-day availability is intentionally sacrificed until a trustworthy intraday source exists.

### D-011 — Canonical fact identity follows immutable raw identity

Canonical instrument identity can be corrected. Fact identity therefore follows provider revision + raw code + field, allowing reassignment or removal without duplicate conflicting facts.

### D-012 — Review issues are repaired, not merely documented

Repeated reviews exposed migration drift, timezone coincidence, poison-record wedges, empty-snapshot erasure, ineffective dirtiness, code-reuse gaps, false ambiguity, corrupt-cache wedges, and identity-reassignment duplication. Correctness and recoverability issues were implemented and regression-tested rather than left as prose-only caveats.

Detailed decision records:

- [`001-tdx-daily-ingestion.md`](decisions/001-tdx-daily-ingestion.md)
- [`002-gbbq-and-adjustment-segments.md`](decisions/002-gbbq-and-adjustment-segments.md)
- [`003-temporal-classification-snapshots.md`](decisions/003-temporal-classification-snapshots.md)
- [`004-security-master-and-content-dirtiness.md`](decisions/004-security-master-and-content-dirtiness.md)
- [`005-partitioned-security-master-resilience.md`](decisions/005-partitioned-security-master-resilience.md)
- [`006-professional-financial-artifacts.md`](decisions/006-professional-financial-artifacts.md)
- [`007-cninfo-filing-and-pit-fundamentals.md`](decisions/007-cninfo-filing-and-pit-fundamentals.md)

# 19. Remaining roadmap

The current A+B evidence/PIT loop is complete for the reviewed FN230–FN238 fields. Remaining work is intentionally separate:

1. authoritative CNINFO numerical extraction/selected-fact validation;
2. authoritative historical security lifecycle;
3. authoritative trading calendar and populated exchange/company masters;
4. broader reviewed financial field mappings and statement dimensions;
5. dedicated index and fund domains;
6. intraday canonical data;
7. derived fundamentals, valuation, factors, screening, and backtesting interfaces.
