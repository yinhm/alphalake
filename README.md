# AlphaLake

AlphaLake is a local-first, reproducible financial market data infrastructure for investment research.

It ingests data from multiple source adapters, normalizes records into a canonical model, stores analytical data in DuckDB, and keeps lineage needed to rebuild, validate, and derive datasets. Stable source files are retained as immutable raw evidence when available.

## Initial scope

- A-share daily OHLCV and market reference data from TDX
- Corporate actions, share-capital changes, classifications, and index/block membership from TDX
- TDX professional financial data as the primary structured fundamental source
- CNINFO filings as an authoritative validation and lineage source
- DuckDB as the canonical analytical store

## Current implementation

The current foundation supports:

- TDX security-master discovery for Shanghai, Shenzhen, and Beijing with exchange-partition fault isolation;
- canonical `instrument_id` resolution while retaining temporal provider identifiers;
- half-open identifier validity intervals and observed code-reuse lifecycle handling;
- two-observation close confirmation so a one-off code-list omission cannot immediately fragment identity;
- strict temporal identifier resolution: overlapping identities are treated as corruption rather than resolved arbitrarily;
- full bootstrap plus per-instrument incremental daily ingestion for equities and ETFs;
- canonical date-only daily semantics independent of host timezone;
- canonical stock/ETF volume in shares/units rather than TDX hands;
- row-level OHLCV quarantine with persisted validation findings and durable retry checkpoints;
- atomic daily publication of valid bars, validation findings, and retry checkpoint state;
- DuckDB Appender + temporary staging + set-based daily upsert inside per-instrument recovery transactions;
- TDX GBBQ corporate-action ingestion with raw category/C1-C4 lineage;
- last-known-good protection against suspicious empty/truncated GBBQ snapshots, with an explicit repair override;
- verified share-capital observations with source-record identity;
- affine QFQ/HFQ adjustment segments derived locally from raw OHLC + corporate actions;
- content-based dirty-input signatures so unchanged adjustment inputs skip historical reload/recalculation even after normal ingestion replay;
- temporal TDX concept/style-region/index-block membership;
- temporal TDX and Shenwan industry hierarchies/memberships with taxonomy-level failure isolation after shared acquisition;
- SHA-256 content-addressed immutable raw-artifact storage with `meta.artifact` lineage;
- TDX professional-financial `gpcw.txt` / `gpcw*.zip` acquisition with listed MD5/size verification;
- dynamic, lossless gpcw parsing: field count comes from `report_size/4`, and original float32 bits are retained;
- bulk `fundamental.provider_fact` persistence with immutable-artifact revisions and report-period temporal identity resolution;
- reviewed TDX FN230–FN238 provider-field mappings;
- durable ingest/calculation run state (`completed`, `partial`, `failed`, `canceled`);
- database-backed operational status and version-gated schema migrations.

Professional-financial provider facts deliberately keep `announcement_time` nullable. The raw gpcw package does not provide an authoritative per-record announcement timestamp, so AlphaLake does **not** infer one from fetch time, filename, or report period and does not yet materialize canonical point-in-time `fundamental.fact` rows.

Indexes and convertible bonds are discovered in the instrument master but deliberately excluded from the first equity/ETF daily and adjustment paths until their request/unit semantics have dedicated tests.

## Build and test

AlphaLake currently uses Go 1.25, driven by the current `github.com/injoyai/tdx` dependency.

```bash
go test ./...
go build ./cmd/alphalake
```

CI also checks that `go mod tidy` produces no changes.

## CLI

Initialize or migrate a DuckDB database:

```bash
alphalake init ./alphalake.duckdb
```

Synchronize one TDX symbol. The first run bootstraps history; later runs use the same incremental/quarantine/lineage semantics as the all-market path:

```bash
alphalake sync-daily ./alphalake.duckdb sh600519
```

Synchronize the current TDX equity/ETF universe. Existing instruments resume from their own latest stored/retry boundary and re-fetch the boundary day. A temporary failure in one exchange partition does not prevent healthy exchange partitions from continuing:

```bash
alphalake sync-daily-all ./alphalake.duckdb
```

Refresh TDX GBBQ corporate-action/share-capital snapshots:

```bash
alphalake sync-actions ./alphalake.duckdb
```

By default AlphaLake refuses a successful-but-empty or suspiciously truncated GBBQ snapshot when a prior snapshot exists. For an explicit operator-approved repair, bypass only that snapshot-size safety guard with:

```bash
alphalake sync-actions ./alphalake.duckdb --force
```

`--force` does **not** bypass acquisition errors, identifier mismatches, or database constraints.

Calculate adjustment segments locally from stored raw OHLC and corporate actions. Unchanged canonical inputs are skipped by content signature:

```bash
alphalake calc-adjustments ./alphalake.duckdb
```

Refresh temporal TDX block classifications:

```bash
alphalake sync-classifications ./alphalake.duckdb
```

Refresh TDX and Shenwan industry hierarchies/memberships from TDX industry assignments and `incon.dat`:

```bash
alphalake sync-industries ./alphalake.duckdb
```

Synchronize TDX professional financial data. The safe default processes only the newest listed gpcw package and retains raw data under `raw/` beside the DuckDB file:

```bash
alphalake sync-financial ./alphalake.duckdb
```

Explicitly backfill every listed historical package:

```bash
alphalake sync-financial ./alphalake.duckdb --all
```

A package gets a completion checkpoint only after every record can be resolved to a canonical instrument. Historical unresolved codes remain retryable from the retained local artifact, so a later lifecycle enrichment does not require redownloading the package.

A normal market refresh sequence is therefore:

```bash
alphalake sync-daily-all ./alphalake.duckdb
alphalake sync-actions ./alphalake.duckdb
alphalake calc-adjustments ./alphalake.duckdb
alphalake sync-classifications ./alphalake.duckdb
alphalake sync-industries ./alphalake.duckdb
alphalake sync-financial ./alphalake.duckdb
```

Inspect the database without mutating it:

```bash
alphalake status ./alphalake.duckdb
```

This reports current/latest schema version, pending migrations, validation failures, checkpoints, and recent ingest runs.

Inspect embedded schema migrations:

```bash
alphalake schema
```

## Data layout

For a database at `./data/market.duckdb`, professional-financial raw artifacts default to:

```text
data/
  market.duckdb
  raw/
    tdx/
      professional_financial/
        <sha-prefix>/
          <sha256>.txt
          <sha256>.zip
```

The paths stored in `meta.artifact` are relative to the configured raw root.

## Principles

- Provider-specific formats stop at source adapters.
- Canonical records use stable instrument identity instead of provider symbols.
- Destructive temporal changes require sufficiently complete, repeated provider evidence; one incomplete or one-off observation must not silently close history.
- Provider partitions fail independently where the source naturally exposes independent partitions.
- Ingestion lineage records provenance, while derived-data dirtiness is determined from canonical content.
- Stable source files/documents are immutable evidence, content-addressed when retained locally.
- Unadjusted OHLC is primary price truth; adjusted values are reproducible derivatives.
- Financial report period and announcement time are distinct; missing announcement time must not be guessed.
- Provider facts may precede canonical PIT facts when source semantics are incomplete.
- Derived datasets are reproducible from canonical facts and their input state.
- Data-quality failures are queryable data, not only log output.

## Design documentation

- [`docs/design.md`](docs/design.md) — accepted target architecture/specification and major design decisions.
- [`docs/implementation-status.md`](docs/implementation-status.md) — factual implemented/partial/schema-only/planned status matrix.
- [`docs/decisions/001-tdx-daily-ingestion.md`](docs/decisions/001-tdx-daily-ingestion.md) — TDX daily ingestion/resumability decisions.
- [`docs/decisions/002-gbbq-and-adjustment-segments.md`](docs/decisions/002-gbbq-and-adjustment-segments.md) — GBBQ snapshots and affine adjustment semantics.
- [`docs/decisions/003-temporal-classification-snapshots.md`](docs/decisions/003-temporal-classification-snapshots.md) — prospective temporal classification decisions.
- [`docs/decisions/004-security-master-and-content-dirtiness.md`](docs/decisions/004-security-master-and-content-dirtiness.md) — verified security-master snapshots, temporal identity, content-based dirtiness, and atomic daily quarantine publication.
- [`docs/decisions/005-partitioned-security-master-resilience.md`](docs/decisions/005-partitioned-security-master-resilience.md) — partition-scoped master refresh, repeated absence confirmation, industry fault isolation, and dead-path cleanup.
- [`docs/decisions/006-professional-financial-artifacts.md`](docs/decisions/006-professional-financial-artifacts.md) — immutable gpcw evidence, lossless provider facts, artifact revisions, temporal financial identity, and announcement-time boundaries.
