from math import nextafter
from statistics import mean
import pytest
from tools.backtest_tdx_multiyear_growth import leave_one_company_out_revenue_nonworse


@pytest.mark.parametrize('values', [[], [(0., 0.)], [(1., 1.), (2., 2.)],
    [(1., nextafter(1., 2.)), (2., 2.), (1e30, 1e30)],
    [(1e30, 1e30), (1e-30, 1e-30), (2., 1.)]])
def test_exact_grouping_matches_original_mean_after_excluding_each_company(values):
    rows = [dict(code=str(i % 3), status='evaluated', errors={
        'baseline': {'revenue_error_pct': a}, 'candidate': {'revenue_error_pct': b}})
        for i, (a, b) in enumerate(values)]
    codes = {r['code'] for r in rows}
    comparisons = []
    for code in codes:
        kept = [r for r in rows if r['code'] != code]
        comparisons.append(bool(kept) and mean(abs(r['errors']['candidate']['revenue_error_pct']) for r in kept)
            <= mean(abs(r['errors']['baseline']['revenue_error_pct']) for r in kept))
    expected = bool(codes) and all(comparisons)
    assert leave_one_company_out_revenue_nonworse(rows + [dict(code='blocked', status='blocked')], 'baseline', 'candidate') == expected
