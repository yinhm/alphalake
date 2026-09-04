# ADR 007 — CNINFO filing evidence and point-in-time fundamentals

Status: accepted

## Context

TDX professional-financial packages provide reproducible structured values and report periods, but the raw `gpcw` package does not provide a trustworthy per-company announcement timestamp. AlphaLake therefore retained provider facts without fabricating `announcement_time` and deliberately left canonical `fundamental.fact` unmaterialized.

CNINFO is the authoritative disclosure platform selected to supply filing identity, disclosure time and original filing-document evidence. It does not replace TDX as the initial structured numerical provider. The two sources retain independent provenance and are joined explicitly.

## Decision 1 — Preserve catalogue pages and filing documents as separate evidence

Every acquired CNINFO catalogue page is retained through the common immutable artifact store before normalized filing metadata is written. Catalogue artifact identity includes the query date window, page number and page size.

By default, original periodic-report documents are also downloaded and retained as content-addressed artifacts. `--metadata-only` is an explicit operational mode that retains announcement metadata without document bytes; it is not the normal evidence-complete path.

The filing row points at the currently observed document artifact while `fundamental.filing_document` retains every immutable document revision associated with the source filing ID.

## Decision 2 — Source filing ID is filing identity

`fundamental.filing` is unique by `(source, source_filing_id)`.

Instrument/report-period pairs are not filing identity because one period may have a full report, summary, corrected report, correction notice, revision, audit report and unrelated announcements. Re-observation of one source filing updates normalized metadata and resolution while retaining `first_seen_at` and source artifact lineage.

Correction/revision observations may point to the immediately preceding eligible report anchor through `corrects_filing_id`, but that relationship does not replace source identity.

## Decision 3 — Filing classification is conservative and versioned

The first classifier recognizes Q1, semiannual, Q3 and annual periodic reports from explicit title wording. It distinguishes full reports, summaries, correction notices, corrected reports and revisions.

Announcements that merely discuss a report — including postponement/reservation notices, inquiry letters, presentations, board resolutions, forecasts and earnings flashes — are retained as filing evidence but cannot anchor PIT facts.

The original provider category and raw catalogue identity fields are retained. `classifier_version` makes future reclassification reproducible.

## Decision 4 — Filing identity uses disclosure-time security evidence

CNINFO security codes are identifiers, not canonical identities.

A filing is resolved against temporal TDX identifiers as of its announcement date. Explicit exchange evidence is used to form an exact provider symbol; if exchange evidence is absent, AlphaLake performs a strict equity-only raw-code search. Present-day code-range heuristics are not used.

Zero matches remain `pending`; multiple eligible matches remain ambiguous/pending; overlapping rows for the same full temporal identifier are store corruption. Pending filings remain queryable and are retried locally before fundamental materialization, so later security-lifecycle enrichment can repair old catalogue windows without redownloading them.

## Decision 5 — Windowed acquisition is resumable but corrections remain discoverable

CNINFO catalogue acquisition is split into bounded date windows and pages. Each page is an immutable artifact. A completed old window may be skipped by checkpoint, while recent windows are rescanned by default so late corrections and revised metadata remain discoverable. `--rescan` explicitly ignores old-window checkpoints.

Catalogue row failures are persisted as validation evidence and do not discard other valid rows from the page. A window checkpoint is published only after all pages and required document acquisitions for that window succeed.

## Decision 6 — Provider values and filing evidence never overwrite each other

TDX provider facts remain TDX facts. CNINFO announcement time is not written back as though TDX supplied it.

`fundamental.provider_filing_link` explicitly links one immutable provider-record revision `(provider source, artifact revision, raw provider code)` to one authoritative filing.

A candidate filing must have:

- the same canonical instrument;
- the same report period;
- a compatible periodic-report type;
- an announcement time no later than the first observation time of that provider artifact.

The last condition prevents a correction announced in the future from being attached to an earlier provider revision. Among eligible candidates, later announcement time wins; at the same time, corrected report/revision/correction notice/full-report priority is deterministic. Equally ranked candidates remain `ambiguous` rather than being selected by filing ID.

## Decision 7 — Canonical facts use reviewed mappings and deterministic precision

Only provider fields with an explicitly reviewed canonical mapping and unit are eligible for `fundamental.fact`.

The initial set remains FN230–FN238:

- FN230–FN237 are monetary facts normalized to CNY yuan;
- FN238 is normalized to shares.

The provider layer remains lossless (`float32` bits plus analytical `float64`). Canonical values use `DECIMAL(38,10)` as a deterministic decimal representation of the provider value. This does not claim recovery of precision that the provider's float32 encoding did not contain.

Statement scope is recorded as `provider_default`; AlphaLake does not assert consolidated/parent-company scope that the provider record did not explicitly distinguish.

## Decision 8 — Canonical materialization is a local reconciliation

`materialize-fundamentals` performs no network access. It:

1. retries retained pending filing identities;
2. refreshes provider-to-filing links;
3. validates linked provider facts;
4. reconciles canonical PIT facts.

Canonical fact identity follows immutable provider-record identity rather than canonical instrument identity. Later identity, mapping or filing corrections therefore update/reassign the existing fact instead of creating conflicting duplicates. Facts whose source evidence is no longer safely linked/materializable are removed, and rejected candidates create queryable validation results.

## Decision 9 — ASOF semantics are first-class

`fundamental.fact_latest` exposes the latest known revision per instrument/field/report period.

`fundamental.fact_asof(as_of_time)` returns the latest revision whose authoritative `announcement_time` is no later than the supplied timestamp. Therefore a report is absent before disclosure, appears after the original filing, and changes only after a later correction/revision announcement.

## Consequences and boundaries

- AlphaLake now has an explicit evidence path from TDX numerical values to CNINFO filing identity and announcement time.
- Original and corrected provider revisions can coexist and are selected correctly by ASOF time.
- Historical backfill cannot recreate provider revisions that AlphaLake never observed. If only the current corrected gpcw bytes exist, AlphaLake materializes only that supported revision; it does not invent an earlier value history.
- Catalogue/document acquisition and canonical materialization are separate commands so raw source ingestion and local derivation remain reproducible.
- Numerical extraction from PDF/XBRL and selected CNINFO-vs-TDX value comparison remain a later validation layer; they are not prerequisites for authoritative announcement-time anchoring.
