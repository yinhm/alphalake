# AlphaLake

AlphaLake is a local-first, reproducible financial market data infrastructure for investment research.

It ingests data from multiple source adapters, preserves raw source artifacts, normalizes records into a canonical model, stores analytical data in DuckDB, and keeps lineage needed to rebuild and validate datasets.

## Initial scope

- A-share daily OHLCV and market reference data from TDX
- Corporate actions, share-capital changes, classifications, and index/block membership from TDX
- TDX professional financial data as the primary structured fundamental source
- CNINFO filings as an authoritative validation and lineage source
- DuckDB as the canonical analytical store

## Principles

- Provider-specific formats stop at source adapters.
- Canonical records use stable instrument identity instead of provider symbols.
- Raw artifacts are immutable and retained for reproducibility.
- Financial data is point-in-time aware: report period and announcement time are distinct.
- Derived datasets are reproducible from canonical facts.

See [`docs/design.md`](docs/design.md) for the current specification and important design decisions.
