# ADR 005 — Partitioned security-master resilience and confirmed temporal closes

Status: accepted

Supersedes the global-completeness/first-absence close mechanics in ADR 004 Decision 1. The temporal identity and content-dirtiness principles in ADR 004 remain in force.

## Context

The first production security-master lifecycle implementation made destructive identity changes only from a verified complete TDX snapshot. That prevented a truncated response from closing history, but two availability problems remained:

1. Shanghai, Shenzhen, and Beijing were coupled into one global completeness gate and one store transaction. A temporary Beijing failure could block valid Shanghai/Shenzhen refreshes, and a store-side guard failure in one exchange could roll back healthy exchanges.
2. A single complete observation in which one code was absent immediately closed its identifier. A transient one-day omission followed by a return would therefore split one real security into two canonical instruments.

The industry acquisition path had a similar coupling problem: TDX and Shenwan hierarchies share acquisition inputs, but a build error in one taxonomy prevented the other from updating.

## Decision 1 — Security-master authority is partition-scoped

`InstrumentMasterSnapshot` carries independent partition state. TDX currently uses exchange partitions:

- `XSHG` / Shanghai;
- `XSHE` / Shenzhen;
- `XBSE` / Beijing.

The adapter may return a usable partial snapshot when at least one partition succeeds. A failed/empty/malformed partition is marked incomplete and has **no destructive authority**; healthy partitions remain usable.

The DuckDB store applies each usable partition in its own transaction. Therefore:

- a source failure in Beijing cannot block Shanghai/Shenzhen;
- a suspicious truncation or store-side validation failure in one partition rolls back only that partition;
- downstream ingestion receives only observations whose partition was successfully applied.

A partition marked complete must be non-empty and still passes the exchange-level size/truncation guard before it may advance removal evidence.

## Decision 2 — Closing an identifier requires repeated absence evidence

A destructive `valid_to` update is not made from one observation.

For each open provider identifier absent from a complete partition:

1. the first missing observation writes a durable `meta.checkpoint` evidence row containing the first missing date;
2. a same-day rerun is not additional evidence;
3. a later complete observation in which the identifier remains absent confirms the close;
4. `valid_to` is set to the **first observed missing date**, preserving the best observed boundary;
5. if the identifier returns before confirmation, the pending evidence is deleted and the identity remains open.

Thus the current close threshold is **two distinct complete observations**. Failed/incomplete partition observations neither create nor advance missing evidence.

If confirming a close would produce a zero/negative interval because of inconsistent historical data, AlphaLake leaves the identifier open and defers the close rather than rolling back valid partition work or inventing an invalid interval.

This is still observation-derived lifecycle evidence, not an authoritative delisting calendar. Missing the entire absence interval remains unrecoverable without another source.

## Decision 3 — Industry taxonomies share acquisition, not failure fate

TDX industry and Shenwan industry share `tdxhy` assignments and `incon.dat`, so those inputs are still fetched once. Shared acquisition failures remain global because neither taxonomy can be built reliably without the common inputs.

After acquisition, taxonomy construction is independent:

- a TDX-industry build error yields a failed TDX result only;
- a Shenwan build error yields a failed Shenwan result only;
- successful taxonomy results continue through the existing temporal classification store;
- a mixed result produces a partial ingest run rather than suppressing all industry updates.

Historical `incon.dat` variants may omit an intermediate hierarchy label. Such unnamed intermediate levels are skipped rather than fabricated; the assigned leaf must still have a real provider name before membership can attach to it.

## Decision 4 — Remove duplicate/dead write semantics

`RecordValidationViolations` was removed after daily ingestion moved to the atomic `ApplyDailyIngestBatchForRun` path. Validation persistence now has one production transaction semantic for daily ingestion rather than a second standalone writer.

The unused `snapshotDateAt` helper was also removed.

## Decision 5 — Partial safety must remain observable, and structure is validated before side effects

Partition fault isolation is not permission to silently shrink the visible universe. Source-side partition errors and store-side partition failures are returned as `PartitionFailures`, persisted as run diagnostics in `meta.validation_result`, and propagated into ingest summaries. A workflow may continue with healthy partitions, but its terminal run status is `partial`, not `completed`.

`InstrumentMasterSnapshot` also has a structural preflight before any partition transaction begins. The preflight verifies:

- flat identifiers are unique and provider-consistent;
- partition exchange MICs are unique;
- each partition identifier exists in the flat snapshot;
- each flat identifier belongs to exactly one partition;
- partition observations match the partition exchange MIC.

A structural mismatch therefore fails with **no database side effects**, instead of discovering representation drift only after an earlier partition has already committed.

Compatibility snapshots without explicit partitions retain their older single-transaction behavior and also retain a global size/truncation guard. Compatibility must not silently mean weaker destructive-safety checks.

## Consequences

- temporary exchange-specific TDX outages degrade only that exchange instead of stopping the whole market refresh;
- a degraded partition is visible in the run summary/status and queryable through `meta.validation_result` rather than being detectable only by comparing universe counts;
- one transient code-list omission no longer causes irreversible instrument fragmentation;
- destructive identity changes now require repeated evidence, matching AlphaLake's last-known-good/safety-first philosophy;
- malformed flat/partition structure cannot leave a partially committed database before returning an error;
- partial master refreshes can proceed, but failed partitions are intentionally frozen until a healthy observation returns;
- exact official list/delist/reuse dates still require an authoritative lifecycle source;
- industry ingestion has taxonomy-level fault isolation after its shared acquisition stage.
