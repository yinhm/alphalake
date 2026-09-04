# AlphaLake

AlphaLake is a local-first, reproducible financial market data infrastructure for investment research.

It ingests data from multiple source adapters, preserves raw source artifacts where the source exposes stable files/documents, normalizes records into a canonical model, stores analytical data in DuckDB, and keeps lineage needed to rebuild and validate datasets.

## Initial scope

- A-share daily OHLCV and market reference data from TDX
- Corporate actions, share-capital changes, classifications, and index/block membership from TDX
- TDX professional financial data as the primary structured fundamental source
- CNINFO filings as an authoritative validation and lineage source
- DuckDB as the canonical analytical store

## Current implementation

Slice 1 currently supports:

- TDX security-master discovery for Shanghai, Shenzhen, and Beijing;
- canonical `instrument_id` resolution while retaining TDX symbols as provider identifiers;
- full-history daily ingestion for equities and ETFs;
- per-instrument incremental daily synchronization with inclusive boundary re-fetch;
- canonical stock/ETF volume in shares/units rather than TDX hands;
- structural OHLCV validation before write;
- persisted validation failures;
- durable ingest-run state (`completed`, `partial`, `failed`, `canceled`);
- lineage from daily observations and validation findings back to their ingest run.

Indexes and convertible bonds are discovered in the instrument master but deliberately excluded from the first all-market daily ingestion path until their request/unit semantics have dedicated tests.

## Build and test

AlphaLake currently uses Go 1.25, driven by the current `github.com/injoyai/tdx` dependency.

```bash
go test ./...
go build ./cmd/alphalake
```

CI also checks that `go mod tidy` produces no changes.

## CLI

Initialize (or migrate) a DuckDB database:

```bash
alphalake init ./alphalake.duckdb
```

Synchronize one TDX symbol's complete daily history:

```bash
alphalake sync-daily ./alphalake.duckdb sh600519
```

Synchronize the current TDX equity/ETF universe. Existing instruments resume from their own latest stored trading day; the boundary day is fetched again and upserted to repair a possible partial current-day observation:

```bash
alphalake sync-daily-all ./alphalake.duckdb
```

Inspect embedded schema migrations:

```bash
alphalake schema
```

## Principles

- Provider-specific formats stop at source adapters.
- Canonical records use stable instrument identity instead of provider symbols.
- Raw artifacts are immutable and retained when practical for the source.
- Financial data is point-in-time aware: report period and announcement time are distinct.
- Derived datasets are reproducible from canonical facts.
- Data-quality failures are queryable data, not only log output.

## Design documentation

- [`docs/design.md`](docs/design.md) — current normative architecture/specification and major design decisions.
- [`docs/decisions/001-tdx-daily-ingestion.md`](docs/decisions/001-tdx-daily-ingestion.md) — accepted decisions for TDX daily ingestion/resumability.
