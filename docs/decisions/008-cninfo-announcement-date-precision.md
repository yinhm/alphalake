# ADR 008 — CNINFO announcement date precision

Status: accepted

Supersedes the intraday-precision implication in ADR 007 while preserving its source-role and PIT-linking decisions.

## Context

The public CNINFO announcement catalogue exposes `announcementTime` as milliseconds, but the observed public contract establishes an announcement date rather than a trustworthy intraday publication instant. Treating that value as exact release time would create false precision and could leak a filing into a same-day historical query before it was actually public.

AlphaLake needs a deterministic PIT boundary that is conservative across daily and intraday research.

## Decision

For filings acquired from the public CNINFO catalogue, AlphaLake stores four distinct pieces of timing evidence:

- `raw_announcement_time_ms` — the unmodified provider value;
- `announcement_date` — the China-local disclosure date represented as a canonical date;
- `announcement_time_precision='date'` — an explicit statement that intraday precision is not established;
- `announcement_time` — the next China-calendar-day boundary, used as the earliest safe PIT availability instant.

Instrument resolution uses `announcement_date`, because security identity should be resolved on the provider's disclosure date rather than on the following conservative availability boundary.

`fundamental.fact_asof(...)` uses `announcement_time`. Therefore a catalogue-derived filing cannot become visible during its disclosure date and becomes visible at the start of the next China calendar day.

A future source with independently verified publication timestamps may set `announcement_time_precision='timestamp'` and provide its exact availability instant without changing the canonical query interface.

## Consequences

- Daily research remains conservative and deterministic.
- Intraday research cannot consume an announcement prematurely merely because the catalogue encoded a date as milliseconds.
- The original provider value remains available for audit and later reinterpretation.
- The system intentionally accepts up to one calendar day of delayed availability rather than introducing look-ahead.
- CNINFO filing identity, document lineage, TDX numerical provenance, and provider-to-filing links remain unchanged.
