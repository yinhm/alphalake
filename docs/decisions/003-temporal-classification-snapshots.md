# ADR 003 — Temporal classification snapshots

Status: accepted

## Context

TDX block and industry APIs primarily expose current classification state. Investment research and historical backtests require point-in-time membership history so that today's industry/concept/index membership is not silently projected backward.

AlphaLake therefore needs to turn repeated current snapshots into temporal canonical facts without inventing precision that the provider did not supply.

## Decisions

### 1. Provider classification structures stop at the TDX adapter

TDX block files are normalized into provider-neutral snapshots:

```text
ClassificationSnapshot
  Taxonomy
  Nodes[]
    SourceNodeCode
    Name
    ParentNodeCode
    SourceSymbol
    Member provider identifiers[]
```

Canonical `taxonomy_id`, `node_id`, and `instrument_id` are resolved by the store/ingest layer, not the source adapter.

### 2. Block families are separate taxonomies

The first block families are:

- `tdx_concept` from `block_gn.dat`;
- `tdx_style_region` from `block_fg.dat`;
- `tdx_index_block` from `block_zs.dat`.

TDX industry and Shenwan-industry taxonomies are handled separately because they use `tdxhy.cfg` assignments plus `incon.dat` code/name hierarchy rather than block membership files.

### 3. Use stable provider node codes where available

For concept/style blocks, AlphaLake prefers the TDX board index code populated by `GetBlockDataWithIndex`.

`block_zs.dat` itself does not contain a stable board index identifier. Until a stronger mapping is available, AlphaLake uses the explicit fallback:

```text
block_zs.dat:<provider board name>
```

This fallback is deliberately visible rather than pretending the name is a stable exchange identifier.

### 4. Membership intervals are half-open

Canonical classification membership uses:

```text
[effective_from, effective_to)
```

An open membership has `effective_to = NULL`.

If a member is present on the September 1 snapshot and absent from a complete September 4 snapshot, its interval becomes:

```text
[2026-09-01, 2026-09-04)
```

This says only what the observations support: by September 4 it is no longer present. AlphaLake does not invent September 3 as the exact removal date.

### 5. Same-day disagreements are corrections, not zero-length history

Classification history is currently date-granular. If a membership is opened by an earlier snapshot on a date and a later complete snapshot on the same date says it is absent, AlphaLake removes that same-day interval instead of storing meaningless `[date,date)` history.

### 6. Only complete snapshots may close memberships

A successful TDX family fetch is marked `Complete=true`.

If acquisition/parsing fails, the family is not represented as an empty snapshot and therefore cannot close previously open membership.

The classification ingest run is allowed to be `partial`: one failed family does not prevent successfully fetched families from updating.

### 7. Unresolved members reject the whole taxonomy snapshot

Before changing membership history, every provider member identifier in the snapshot must resolve to a canonical instrument.

If any member is unresolved, the transaction is rolled back. This is safer than partially applying a snapshot that would then look complete and could incorrectly close valid members.

### 8. Snapshot dates use the China market calendar day

The observation instant is stored as a timezone-aware timestamp, while membership effective dates are date-granular. AlphaLake derives the snapshot date using UTC+8 (Asia/Shanghai market calendar), independent of the machine/user timezone.

### 9. Current TDX block snapshots are prospective history

AlphaLake cannot reconstruct classification history that TDX no longer exposes unless a separate historical source is added.

Therefore the temporal membership history is authoritative from the date AlphaLake starts observing snapshots forward. It must not be presented as historical truth for earlier periods without another evidence source.

## Consequences

- Historical screens can avoid using future/current memberships once AlphaLake has observed the relevant period.
- Repeated unchanged snapshots are idempotent.
- Removals and re-additions create separate intervals.
- Provider outages do not erase history.
- Industry hierarchies can reuse the same canonical temporal store while keeping their source-specific parser separate.
