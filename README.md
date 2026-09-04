# AlphaLake

AlphaLake is a local-first, reproducible financial market data infrastructure for investment research.

It ingests data from multiple source adapters, normalizes records into a canonical model, stores analytical data in DuckDB, and keeps lineage needed to rebuild, validate, and derive datasets. Immutable raw-artifact retention is an accepted target for sources that expose stable files/documents; see the implementation-status document for what is live today.

## Initial scope

- A-share daily OHLCV and market reference data from TDX
- Corporate actions, share-capital changes, classifications, and index/block membership from TDX
- TDX professional financial data as the primary structured fundamental source
- CNINFO filings as an authoritative validation and lineage source
- DuckDB as the canonical analytical store

## Current implementation

The current market-data foundation supports:

- TDX security-master discovery for Shanghai, Shenzhen, and Beijing;
- canonical `instrument_id` resolution while retaining temporal provider identifiers;
- half-open identifier validity intervals and safe code-reuse semantics in the store;
- full bootstrap plus per-instrument incremental daily ingestion for equities and ETFs;
- canonical date-only daily semantics independent of host timezone;
- canonical stock/ETF volume in shares/units rather than TDX hands;
- row-level OHLCV quarantine with persisted validation findings and durable retry checkpoints;
- DuckDB Appender + temporary staging + set-based daily upsert inside per-instrument recovery transactions;
- TDX GBBQ corporate-action ingestion with raw category/C1-C4 lineage;
- last-known-good protection against suspicious empty/truncated GBBQ snapshots;
- verified share-capital observations with source-record identity;
- affine QFQ/HFQ adjustment segments derived locally from raw OHLC + corporate actions;
- dirty-input signatures so unchanged adjustment inputs skip historical reload/recalculation;
- temporal TDX concept/style-region/index-block membership;
- durable ingest/calculation run state (`completed`, `partial`, `failed`, `canceled`);
- database-backed operational status and version-gated schema migrations.

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

Synchronize the current TDX equity/ETF universe. Existing instruments resume from their own latest stored/retry boundary and re-fetch the boundary day:

```bash
alphalake sync-daily-all ./alphalake.duckdb
```

Refresh TDX GBBQ corporate-action/share-capital snapshots:

```bash
alphalake sync-actions ./alphalake.duckdb
```

Calculate adjustment segments locally from stored raw OHLC and corporate actions. Unchanged instruments are skipped by input signature:

```bash
alphalake calc-adjustments ./alphalake.duckdb
```

Refresh temporal TDX block classifications:

```bash
alphalake sync-classifications ./alphalake.duckdb
```

A normal market refresh sequence is therefore:

```bash
alphalake sync-daily-all ./alphalake.duckdb
alphalake sync-actions ./alphalake.duckdb
alphalake calc-adjustments ./alphalake.duckdb
alphalake sync-classifications ./alphalake.duckdb
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

## Principles

- Provider-specific formats stop at source adapters.
- Canonical records use stable instrument identity instead of provider symbols.
- Stable source files/documents should be retained immutably when that acquisition path is implemented.
- Unadjusted OHLC is primary price truth; adjusted values are reproducible derivatives.
- Financial data is point-in-time aware: report period and announcement time are distinct.
- Derived datasets are reproducible from canonical facts and their input lineage.
- Data-quality failures are queryable data, not only log output.

## Design documentation

- [`docs/design.md`](docs/design.md) — accepted target architecture/specification and major design decisions.
- [`docs/implementation-status.md`](docs/implementation-status.md) — factual implemented/partial/schema-only/planned status matrix.
- [`docs/decisions/001-tdx-daily-ingestion.md`](docs/decisions/001-tdx-daily-ingestion.md) — TDX daily ingestion/resumability decisions.
- [`docs/decisions/002-gbbq-and-adjustment-segments.md`](docs/decisions/002-gbbq-and-adjustment-segments.md) — GBBQ snapshots and affine adjustment semantics.
- [`docs/decisions/003-temporal-classification-snapshots.md`](docs/decisions/003-temporal-classification-snapshots.md) — prospective temporal classification decisions.
