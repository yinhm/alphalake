# AlphaLake Design

Status: draft v0

This document intentionally contains both the **current specification** and the **important design decisions that led to it**. The specification is normative for implementation; the decision log records alternatives, trade-offs, and reasons so later changes can be made deliberately rather than by accident.

## 1. Final specification

### 1.1 Product definition

AlphaLake is a local-first financial data lake and analytical store for reproducible investment research. It is not a trading execution system and is not tied to a single vendor, exchange, country, or database file format.

The first implementation targets A-share research using TDX as the primary structured data source and CNINFO as an authoritative filing/validation source.

### 1.2 Architectural boundaries

```text
External sources
    |
    +-- TDX -----------+
    |                  |
    +-- CNINFO --------+----> source adapters ----> canonical domains ----> DuckDB
    |                  |             |
    +-- future sources +             +----> immutable raw artifacts
```

A source adapter has two responsibilities:

1. acquire source data and preserve a reproducible raw artifact when applicable;
2. translate provider-specific records into AlphaLake canonical records.

Provider SDK types must not escape the adapter package.

### 1.3 Source roles

#### TDX

TDX is the initial primary source for:

- security/code discovery needed to bootstrap the A-share universe;
- daily OHLCV and later intraday data;
- corporate actions and share-capital history (GBBQ);
- TDX classifications, blocks, and index membership;
- professional financial data (`tdxfin/gpcw*.zip` family);
- optional F10/current finance snapshots as supplementary data.

TDX-specific transport, report-file protocol, binary codecs, and local file parsing should use `github.com/injoyai/tdx` where practical. AlphaLake owns canonical units and semantics.

#### CNINFO

CNINFO is initially an authoritative evidence and validation source, not the primary structured financial provider.

It is used for:

- filing catalogue and original report documents;
- announcement/disclosure timestamps;
- validation of selected high-value financial facts;
- lineage from canonical facts to authoritative filings.

### 1.4 Canonical identity

Provider symbols are identifiers, not primary identities.

`ref.instrument` owns a stable `instrument_id`. Provider-specific identifiers are stored in `ref.instrument_identifier` with validity intervals.

Examples mapping to one instrument:

- `sh600519` (TDX)
- `600519` + XSHG (exchange ticker)
- possible future ISIN/vendor identifiers

A provider symbol change must not change `instrument_id`.

### 1.5 Domain schemas

DuckDB uses domain-oriented schemas:

- `meta` — schema version, ingestion runs, artifacts, checkpoints, validation results;
- `ref` — instruments, identifiers, exchanges, calendars, companies;
- `market` — OHLCV, corporate actions, share capital, adjustment data;
- `fundamental` — filings, raw provider facts, canonical facts and mappings;
- `classification` — taxonomies, nodes, temporal membership;
- `fund` — fund master/NAV/AUM/holdings (later slice);
- `index` — index master, temporal constituents and weights (later slice);
- `derived` — reproducible research/valuation/factor datasets;
- `staging` — transient normalized batches before canonical merge.

### 1.6 Raw artifacts and lineage

Raw source artifacts are immutable whenever the source offers a stable file or document. Each artifact records at least:

- source;
- dataset;
- source locator/name;
- fetched timestamp;
- content hash;
- content length;
- parser version or ingest version when parsed.

Canonical facts retain source lineage (`source`, source record/file identifier, ingest run). Reprocessing a raw artifact with a newer parser must be possible without redownloading it.

### 1.7 Market data semantics

`market.ohlcv_daily` stores unadjusted prices only.

Canonical units:

- prices/amount: native currency units using exact/decimal-compatible database types where practical;
- volume: shares/units, not TDX "hands";
- dates/times: exchange-local observation date/time plus timezone-aware ingestion metadata.

Adjusted prices are derived from raw OHLCV plus corporate-action semantics. Provider high-level adjusted-price helpers may be used for validation, not as the canonical stored truth.

### 1.8 Corporate actions

TDX GBBQ decoding may come from `injoyai/tdx`, but AlphaLake owns interpretation.

This is required because provider libraries may normalize or omit semantics that matter to the lake, including ETF split/scale events such as category 11. Canonical corporate-action records must preserve the original category and source fields in addition to normalized meaning.

### 1.9 Classification and membership

Classification is temporal.

A membership record contains an effective interval, not only today's state. Daily/scheduled snapshots may be diffed to create history when the source only exposes current membership.

This applies to:

- TDX industry/concept/style/geography;
- future standardized taxonomies;
- index constituents.

The design prevents look-ahead and survivorship bias in historical research.

### 1.10 Financial data model

Financial data is point-in-time aware.

At minimum, the model distinguishes:

- `report_period` — period the statement describes;
- `announcement_time` — when the market could have known it;
- `ingested_at` — when AlphaLake acquired it;
- revision/restatement identity where detectable.

TDX professional financial fields are first retained losslessly as provider facts, e.g. `FN230`, rather than immediately becoming hundreds of physical columns.

Provider mapping is explicit:

```text
TDX FN230 -> canonical revenue
TDX FN232 -> canonical net_income_parent
...
```

This allows field definitions to evolve without changing the binary parser.

Canonical financial facts include enough dimensions to distinguish statement/period semantics and to support point-in-time queries.

### 1.11 TDX adapter boundary

Only `internal/source/tdx` may directly import `github.com/injoyai/tdx`.

The adapter converts library types into AlphaLake-neutral types. It must correct/retain semantics where the SDK's convenience representation differs from AlphaLake, for example:

- convert stock volume back to shares/units if an SDK normalizes it to hands;
- preserve raw GBBQ categories and fields;
- avoid the SDK's private SQLite cache/scheduler as AlphaLake owns persistence and workflow;
- retain additional `.day` metadata when needed even if a high-level SDK type does not expose it.

If the upstream library lacks a lossless capability, prefer contributing/forking the codec there rather than duplicating the protocol inside AlphaLake.

### 1.12 Professional financial files

AlphaLake should support the TDX professional financial file family (`tdxfin/gpcw.txt` and `gpcwYYYYMMDD.zip`).

The parser must derive field count from the file header/report size. It must not hard-code historical fixed counts such as 264 fields.

The parser layer outputs provider fields (`FN1...FNn`) and source metadata. Canonical financial mapping happens one layer above it.

### 1.13 Validation

Validation is data, not only logging.

Validation results should be persisted with source, rule, observation, severity, and run identity.

Initial checks include:

Market:
- high >= open/close/low;
- low <= open/close/high;
- volume >= 0;
- uniqueness by instrument/date/source policy.

Financial:
- selected accounting identities within tolerance;
- high-value facts cross-checked against CNINFO filings;
- TDX announcement date compared with CNINFO disclosure time where possible.

### 1.14 Ingestion workflow

Workflow is dataset-oriented and resumable.

Each dataset defines:

- dependencies;
- checkpoint semantics;
- acquisition step;
- parsing/normalization step;
- validation step;
- canonical merge step.

Do not use a single global `MAX(date)` rule for every dataset. Financial updates must be able to detect corrections/restatements to older periods, commonly by rescanning a recent announcement window and deduplicating by source identity/hash.

### 1.15 Initial delivery slices

#### Slice 0 — foundation
- repository layout;
- design/spec and decision log;
- SQL schema bootstrap;
- source/domain/store boundaries;
- minimal CLI.

#### Slice 1 — TDX daily market data
- instrument bootstrap;
- daily OHLCV acquisition/import;
- immutable raw artifacts;
- market validation.

#### Slice 2 — corporate actions/classification
- raw GBBQ ingestion;
- normalized corporate actions/share capital;
- TDX classifications and temporal membership.

#### Slice 3 — TDX professional financial data
- financial file list/download;
- lossless `gpcw` parser;
- FN field catalogue/mapping;
- point-in-time canonical facts.

#### Slice 4 — CNINFO validation
- filing catalogue;
- document lineage;
- announcement-time validation;
- selected fact validation.

### 1.16 Non-goals for v0

- real-time execution/order management;
- data redistribution service;
- vendor-specific business logic outside adapters;
- introducing EastMoney/Tushare as required sources;
- storing adjusted prices as primary facts;
- supporting multiple analytical database backends.

## 2. Important design decisions

### D-001 — DuckDB is the single canonical analytical store

**Decision:** Use DuckDB rather than a generic storage abstraction or Pebble as the primary database.

**Why:** The workload is increasingly relational/analytical: historical OHLC, financial statements, point-in-time joins, classification membership, fund/index constituents, factor preparation, screening and backtests. SQL, window functions, ASOF joins, columnar scans and Parquet interoperability are more valuable than raw KV control.

**Rejected:** Pebble as the only store. It remains excellent for low-level KV workloads, but would force AlphaLake to implement query/index/join/aggregation semantics itself.

**Consequence:** Do not build a ClickHouse/DuckDB repository abstraction in v0. Abstract sources, not the already-selected store.

### D-002 — Source adapters, not source-shaped domain models

**Decision:** TDX is a source adapter; its symbol/classes/structs do not define the canonical schema.

**Why:** A source-shaped schema becomes expensive when adding filings, funds, other markets, or a second provider. The stable abstraction is the investment-data domain, not a provider API.

**Consequence:** provider types stop at `internal/source/<provider>`.

### D-003 — Reuse `injoyai/tdx` for TDX-specific codec/protocol work

**Decision:** Prefer `github.com/injoyai/tdx` for network protocol, report-file transfer, local TDX formats and raw GBBQ/block codecs.

**Why:** Maintaining duplicate byte-level protocol implementations is high-cost and error-prone. The library already covers broad TDX functionality and is actively maintained.

**Caveat:** High-level convenience semantics are not canonical. Examples observed during design include volume normalization to hands and GBBQ convenience logic that does not fully model all ETF events needed by AlphaLake.

**Consequence:** keep a thin AlphaLake adapter and tests against known TDX/tdx2db edge cases.

### D-004 — Keep `tdx2db` as a reference/test oracle, not a runtime dependency

**Decision:** Borrow proven data-engineering edge cases and workflow ideas from `tdx2db`, but do not copy its provider-shaped schema or depend on it at runtime.

**Why:** It contains valuable behavior around daily files, GBBQ, ETF category 11, calendars and update workflows, but its primary key/model is centered on TDX symbols and it historically supported multiple DB backends that AlphaLake does not need.

### D-005 — TDX professional financial data is primary; CNINFO is authoritative evidence

**Decision:** Do not introduce EastMoney or Tushare in v0. Use TDX professional financial files for structured facts and CNINFO for original filings and verification.

**Why:** This keeps the initial trust model simple: one structured primary source plus one authoritative evidence source. It avoids premature provider proliferation while preserving the ability to validate.

### D-006 — `GetFinanceInfo` is a snapshot, not the historical financial database

**Decision:** Treat TDX `GetFinanceInfo`/F10 as supplementary data only.

**Why:** Historical research requires multiple report periods, announcement time and revision semantics. TDX professional financial files are the correct source family for that use case.

### D-007 — Point-in-time semantics are first-class

**Decision:** Store report period separately from announcement/disclosure time.

**Why:** Backtests must only use data available at the historical decision time. Querying by `report_period <= trade_date` introduces look-ahead bias.

### D-008 — Immutable raw artifacts

**Decision:** Preserve source files/documents and hashes before normalization where practical.

**Why:** Parser fixes should not require redownloading data. It also enables audit, reproducibility, and evidence retention.

**Trade-off:** uses more disk. Local market/fundamental data size makes this acceptable compared with the value of reproducibility.

### D-009 — Canonical instrument ID instead of provider symbol as identity

**Decision:** Introduce `instrument_id` immediately.

**Why:** Provider codes differ and codes/names can change. A stable identity prevents downstream tables from coupling to TDX naming.

### D-010 — Store provider financial facts vertically before canonical mapping

**Decision:** retain `FNxxx -> value` provider facts and map them into canonical facts.

**Why:** TDX financial field counts and definitions evolve. A hard-coded giant wide table couples storage migrations to provider evolution.

**Consequence:** frequently-used canonical facts may later be exposed through views/materialized derived tables for ergonomics/performance.

### D-011 — Classification/index membership is temporal

**Decision:** model effective intervals, not only latest membership.

**Why:** current-only membership creates survivorship and look-ahead bias. Snapshot diffing can recover history prospectively even when an upstream API only exposes current state.

### D-012 — Small, reviewable commits

**Decision:** implementation proceeds in vertical slices with narrow commits: docs, schema, adapter boundary, one dataset at a time.

**Why:** data semantics are subtle. Small commits make regressions and design changes easier to audit and revert.

## 3. Open questions

These are deliberately not settled in v0 foundation work:

- exact DuckDB decimal types/precision for all financial and price fields;
- whether `instrument_id` should be sequence-backed BIGINT or application-generated UUID/ULID;
- how much of TDX professional-financial field metadata should be generated from an upstream catalogue versus curated manually;
- CNINFO parser depth in v0 (metadata validation only vs structured PDF/XBRL extraction);
- artifact retention/compression policy for minute/tick data if those become large.
