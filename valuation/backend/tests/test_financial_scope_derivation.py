import copy
import pytest
from tools.derive_financial_scope import derive, FORMULAS


def test_financial_scope_pairs_claims_and_rejects_missing_or_wrong_evidence():
    rows = {}
    for basis, terms in FORMULAS.values():
        for f in terms:
            rows[f] = dict(field=f, status='available', instrument_id=1, unit='CNY',
                           announcement_time='2026-08-15T16:00:00+00:00',
                           period_basis=basis, balance_date='2026-06-30' if basis=='instant' else None, value='100')
    s = dict(contract_version='alphalake-financial-statements-v1', identity_status='resolved',
             statement_scope='provider_default', code='600519', report_period='2026-06-30',
             information_as_of='2026-09-26T08:00:00Z', statements={'test': list(rows.values())})
    original = copy.deepcopy(s)
    r = derive(s)
    assert r['calculations']['financial_pool_less_deposits']['value_cny'] == '600'
    assert not r['valuation_approved'] and s == original
    rows['deposits_and_interbank_placements']['status'] = 'source_zero_ambiguous'
    assert derive(s)['calculations']['financial_pool_less_deposits']['value_cny'] is None
    rows['deposits_and_interbank_placements']['status'] = 'available'
    for field, key, value in [('funds_lent','unit','shares'), ('funds_lent','balance_date','2025-12-31'),
                              ('funds_lent','instrument_id',2), ('funds_lent','value','NaN'),
                              ('funds_lent','announcement_time','2026-10-01T00:00:00+00:00'),
                              ('financial_business_interest_income','period_basis','quarter')]:
        previous = rows[field][key]
        rows[field][key] = value
        with pytest.raises(ValueError):
            derive(s)
        rows[field][key] = previous
