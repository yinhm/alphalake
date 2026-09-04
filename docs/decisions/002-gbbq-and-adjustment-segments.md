# ADR 002 — TDX GBBQ snapshots and affine adjustment segments

Status: accepted

## Context

AlphaLake needs corporate actions, share-capital history, and reproducible QFQ/HFQ transformations without storing adjusted OHLC as primary facts.

TDX GBBQ contains several event categories with different semantics. Some categories have been verified to carry price-adjustment semantics; others only describe share-capital events. Upstream convenience libraries may also cache derived factors in private databases, which would duplicate AlphaLake's own persistence/workflow layer.

## Decisions

### 1. Fetch GBBQ per canonical instrument instead of calling a black-box all-market helper

AlphaLake refreshes its own instrument master, then calls the TDX SDK for each eligible symbol. This keeps failures attributable to a symbol and permits partial/resumable runs.

Initial eligible types are equity and ETF.

### 2. Persist complete provider snapshots atomically per instrument

After a symbol's GBBQ history is fetched successfully, AlphaLake replaces that instrument/source corporate-action and share-capital snapshot inside one transaction.

Why:

- TDX may correct or remove historical events;
- append-only upsert would leave removed/stale facts behind;
- a failed fetch must not erase the last good snapshot.

### 3. Preserve raw GBBQ identity and fields

Every corporate action retains:

- TDX category;
- raw C1-C4;
- stable source_record_id;
- ingest_run_id.

Canonical semantics are additional interpretation, not a replacement for raw evidence.

### 4. Only verified categories create share-capital facts

Categories 2, 3, 5, 7, 8, 9, and 10 are treated as carrying before/after float/total share counts and may emit `market.share_capital` rows.

Other categories remain corporate actions only unless their share-count meaning is separately verified.

### 5. Price adjustment uses verified categories only

The initial adjustment method is `affine_gbbq_v1`.

It consumes:

- category 1 / canonical `distribution`;
- category 11 / canonical `scale` (ETF/LOF unit conversion).

Category 12 is retained as raw/canonical corporate-action data but is not applied to prices in v1 because its traded-price effect has not been verified to the same standard.

### 6. Use affine transforms rather than scalar factors

For a distribution event:

```text
after = (before - c) / m
m = (10 + bonus_per_10 + rights_per_10) / 10
c = (cash_dividend_per_10 - rights_per_10 * rights_price) / 10
```

For category-11 ETF scale:

```text
after = before / scale
```

AlphaLake stores segment coefficients:

```text
adjusted_price = mul * raw_price + add
```

This preserves cash-dividend additive effects exactly; a scalar-only factor cannot generally do that.

### 7. Adjustment events map to the next trading day

If a GBBQ event date is not itself present in daily OHLC (weekend, holiday, suspension gap), it becomes effective at the first stored trading day on or after the event date.

This prevents events inside suspension gaps from disappearing.

### 8. QFQ/HFQ are derived segments, not primary OHLC

QFQ is anchored to the latest available trading-date regime at `(mul=1, add=0)` and propagated backward across events.

HFQ is normalized so the earliest available regime is `(mul=1, add=0)`.

Only coefficient-change boundaries are stored in `market.adjustment_segment`; daily adjusted prices are computed on demand or in later derived views.

Historical segment `effective_to` is inclusive. The latest segment has `effective_to = NULL`.

### 9. Derived adjustment snapshots are rebuildable and run-linked

`calc-adjustments` reads only canonical DuckDB data:

```text
market.ohlcv_daily
+
market.corporate_action
→
market.adjustment_segment
```

It performs no network access. Each rebuild has a `meta.ingest_run` and stores `ingest_run_id` on adjustment segments.

## Consequences

- Raw OHLC remains the stable source of price truth.
- Corporate-action corrections can trigger deterministic factor rebuilds.
- ETF category-11 events are not lost.
- Unsupported/uncertain categories remain inspectable without silently affecting prices.
- The factor algorithm can evolve under a new `method` value without overwriting prior semantics.
