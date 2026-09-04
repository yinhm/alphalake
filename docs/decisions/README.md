# AlphaLake architecture decisions

The accepted target specification is in [`../design.md`](../design.md). This directory preserves the major decision process and the review-driven changes that produced the current design.

1. [`001-tdx-daily-ingestion.md`](001-tdx-daily-ingestion.md) — TDX daily ingestion, canonical dates/units, resumability, and quarantine.
2. [`002-gbbq-and-adjustment-segments.md`](002-gbbq-and-adjustment-segments.md) — full-snapshot GBBQ semantics and affine QFQ/HFQ derivation.
3. [`003-temporal-classification-snapshots.md`](003-temporal-classification-snapshots.md) — temporal taxonomy membership and incomplete-snapshot safety.
4. [`004-security-master-and-content-dirtiness.md`](004-security-master-and-content-dirtiness.md) — temporal security identity, content-based derived state, and atomic daily publication.
5. [`005-partitioned-security-master-resilience.md`](005-partitioned-security-master-resilience.md) — exchange-partition isolation and repeated absence confirmation.
6. [`006-professional-financial-artifacts.md`](006-professional-financial-artifacts.md) — immutable gpcw evidence, lossless provider facts, and financial identity governance.
7. [`007-cninfo-filing-and-pit-fundamentals.md`](007-cninfo-filing-and-pit-fundamentals.md) — CNINFO filing evidence, provider-filing links, canonical PIT facts, and ASOF queries.
8. [`008-cninfo-announcement-date-precision.md`](008-cninfo-announcement-date-precision.md) — conservative date-precision availability for the public CNINFO catalogue.

Later ADRs may supersede a narrow part of an earlier decision. The later ADR must state that relationship explicitly; supersession does not erase the historical reasoning.
