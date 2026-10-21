"""比较两次真实宏观发布；修订差额不冒充经济环比或公司预测。"""
from collections import Counter
from datetime import timezone
from decimal import localcontext

from data_sources.macro_pages import MacroPageError, read_page

KINDS = ('new_period', 'coverage_added', 'historical_added', 'value_revised', 'scope_changed',
         'source_changed', 'removed_observation', 'definition_changed')
SEMANTIC_FIELDS = ('source', 'dataset', 'geography', 'frequency', 'period_basis',
                   'unit', 'price_basis', 'seasonal_adjustment', 'definition')


def compare(before_id, after_id):
    before, after = read_page(before_id), read_page(after_id)
    if before.id == after.id or before.published_at > after.published_at:
        raise MacroPageError(400, '请选择早于本期的不同发布作为比较基准')
    old = {s.series_code: s for s in before.snapshot.series}
    new = {s.series_code: s for s in after.snapshot.series}
    releases = [{r.release_id: r for r in p.snapshot.releases} for p in (before, after)]

    def side(point, group):
        if point is None:
            return None
        release = releases[group][point.release_id]
        return dict(period_start=point.period_start.isoformat(), value=None if point.value is None else str(point.value),
                    value_status=point.value_status, source_url=release.url, source_sha256=release.sha256,
                    source_locator=point.source_locator, source_published_at=None if release.source_published_at is None else release.source_published_at.astimezone(timezone.utc).isoformat(),
                    publication_precision=release.publication_precision)

    changes = []
    for code in sorted(old.keys() | new.keys()):
        a, b = old.get(code), new.get(code)
        series = b or a
        metadata = dict(series_code=code, name_zh=series.name_zh, unit=series.unit)
        if a and b and any(getattr(a, k) != getattr(b, k) for k in SEMANTIC_FIELDS):
            changes.append(metadata | dict(kind='definition_changed', period_end=None, before=None, after=None,
                delta=None, changed_fields=[k for k in SEMANTIC_FIELDS if getattr(a, k) != getattr(b, k)]))
            continue
        left = {p.period_end: p for p in a.points} if a else {}
        right = {p.period_end: p for p in b.points} if b else {}
        last = max(left, default=None)
        for day in sorted(left.keys() | right.keys()):
            x, y = left.get(day), right.get(day)
            previous, current = side(x, 0), side(y, 1)
            delta = None
            if x is None:
                kind = 'coverage_added' if last is None else 'new_period' if day > last else 'historical_added'
            elif y is None:
                kind = 'removed_observation'
            elif x.period_start != y.period_start:
                kind = 'scope_changed'
            elif (x.value, x.value_status) != (y.value, y.value_status):
                kind = 'value_revised'
                if x.value is not None and y.value is not None:
                    with localcontext() as context:
                        context.prec = 50
                        delta = str(y.value - x.value)
            elif previous != current:
                kind = 'source_changed'
            else:
                continue
            changes.append(metadata | dict(kind=kind, period_end=day.isoformat(), before=previous,
                                           after=current, delta=delta, changed_fields=[]))
    counts = Counter(item['kind'] for item in changes)
    return dict(contract='alphalake-macro-comparison-v1',
                before=dict(id=before.id, content_hash=before.content_hash, published_at=before.published_at.isoformat()),
                after=dict(id=after.id, content_hash=after.content_hash, published_at=after.published_at.isoformat()),
                commentary_changed=before.commentary != after.commentary,
                summary={kind: counts[kind] for kind in KINDS}, total=len(changes), changes=changes)
