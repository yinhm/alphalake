# ADR-001: TDX daily ingestion and resumability

- Status: Accepted
- Date: 2026-09-04
- Scope: Slice 1 — TDX daily market data

## Context

AlphaLake's first production-facing ingestion path must bootstrap the A-share security master, ingest historical daily bars, and support repeated daily updates without re-downloading full history for every instrument.

The implementation also needs to preserve canonical units, survive isolated provider/data errors, and remain auditable through DuckDB metadata.

## Decisions

### 1. Go 1.25 is the project baseline for the current implementation

The foundation initially targeted Go 1.23 so the repository could compile without external dependencies. Once `github.com/injoyai/tdx` became the runtime TDX SDK, AlphaLake moved to Go 1.25 because the current upstream module requires it.

This is a dependency-driven choice, not a general requirement that AlphaLake always track the newest Go release.

### 2. Daily synchronization uses a per-instrument canonical boundary

The resumable boundary for one source/instrument is:

```sql
max(market.ohlcv_daily.trade_date)
WHERE instrument_id = ? AND source = ?
```

AlphaLake does not use one global `MAX(date)` checkpoint for the whole market because instruments can have different histories, suspensions, failures, listing dates, and partial previous runs.

A failed symbol therefore does not move a global cursor past its own missing data.

### 3. The latest stored trading day is re-fetched inclusively

Incremental TDX fetch stops when it reaches the latest stored day, but that boundary record is retained and upserted again.

Reason: a previous run may have occurred during market hours and stored an incomplete current-day K-line. Inclusive re-fetch lets the next run repair that row automatically.

The canonical primary key `(instrument_id, trade_date, source)` makes this repair idempotent.

### 4. Slice 1 all-market daily sync covers equities and ETFs only

The initial all-market loop ingests:

- A-share equities;
- ETFs/LOFs classified as ETF by the TDX SDK.

It deliberately skips indexes and convertible bonds for now.

Reason: TDX uses different request/volume semantics for indexes, while convertible bonds also have unit-specific behavior. Adding them before explicit canonical-unit tests would risk silently mixing incompatible volume meanings.

The instrument master may still contain those instruments; only daily market ingestion is deferred.

### 5. Canonical stock/ETF daily volume is stored in shares/units

`injoyai/tdx` exposes standard stock/ETF daily K-line volume in hands. Its own newer `extend/pull` implementation converts those values with `ToShares` before storage.

AlphaLake therefore converts stock/ETF K-line volume as:

```text
TDX hands × 100 -> canonical shares/units
```

Provider convenience units never become AlphaLake's canonical storage contract.

### 6. Security-master refresh is batched in one DuckDB transaction

TDX code discovery can return thousands of instruments. AlphaLake resolves/upserts the resulting `InstrumentObservation` set in one transaction instead of opening one transaction per symbol.

Returned canonical IDs preserve input order so ingestion can associate each source observation with its resolved `instrument_id` without provider-specific IDs leaking downstream.

### 7. One instrument failure does not abort the whole market

After the security master is refreshed, daily ingestion treats each eligible instrument as an independent recoverable unit.

Provider errors, validation failures, or write errors are collected in `TDXDailySyncSummary.Failures`; subsequent symbols continue.

The overall ingest run is:

- `completed` when all attempted symbols succeed;
- `partial` when at least one symbol succeeds and at least one fails;
- `failed` when the run fails without useful symbol-level progress;
- `canceled` on context cancellation/deadline.

A later run naturally retries failed symbols from their own last canonical boundary.

### 8. Validation failures are persisted, successful row checks are not

Structural OHLCV validation runs before canonical write. Current rules include OHLC bounds, finite prices, non-negative volume/amount, and required identity fields.

Failures are written to `meta.validation_result` and linked to the current `ingest_run_id`.

V0 does not persist a success record for every good bar because doing so would multiply metadata volume without adding equivalent audit value. A successful ingest plus absence of violations is the pass signal.

### 9. Market rows record their latest ingest run

When a tracked run inserts or refreshes `market.ohlcv_daily`, the row records the `ingest_run_id` responsible for that latest write.

This supports lineage in both directions:

```text
ingest run -> rows/validation findings
row -> ingest run that last wrote it
```

### 10. Initial all-market synchronization is sequential

The first correct implementation intentionally processes eligible instruments sequentially over one source abstraction.

Reason: concurrency should not be introduced until connection-sharing/thread-safety behavior of the chosen TDX client strategy is explicit. The upstream project has a `Manage`/pool abstraction that may be a better future concurrency boundary than issuing concurrent requests against one `tdx.Client`.

Correct resumability and semantics take priority over first-run throughput.

## Consequences

### Positive

- partial failures are naturally resumable;
- no global checkpoint can hide symbol-specific gaps;
- current-day partial observations self-repair;
- data-quality failures are queryable instead of being log-only;
- rows and findings have durable run lineage;
- unit semantics are explicit before adding more instrument classes.

### Costs / follow-up work

- a first full-market history load is sequential and may be slow;
- progress reporting should be added before treating long-running CLI sync as polished UX;
- index and convertible-bond daily ingestion need dedicated canonical-unit tests;
- a future connection-pool implementation can add bounded concurrency without changing canonical storage semantics.

## Rejected alternatives

### One global daily checkpoint

Rejected because a successful majority of symbols could advance the checkpoint past a failed/suspended/newly listed symbol and make recovery ambiguous.

### Increment from `latest_date + 1 day`

Rejected because it cannot repair a partial latest-day bar.

### Fail-fast on the first symbol error

Rejected because one provider anomaly would prevent thousands of unrelated instruments from updating.

### Parallel requests on one TDX client immediately

Rejected until concurrency guarantees are established. Silent protocol corruption or race behavior is a worse failure mode than a slower first implementation.
