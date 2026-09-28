from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from tools.derive_tdx_capital import derive, FIELDS, FLOW_FIELDS

PERIOD = date(2025, 12, 31)
CUTOFF = datetime(2026, 9, 27, tzinfo=timezone.utc)


def facts():
    values = {f: '10' for f in FIELDS}
    values.update(reported_ebit='100', reported_ebitda='130', current_assets='100',
                  current_liabilities='200', inventory_decrease_cashflow='-100')
    return [dict(field=f, canonical_field=f, instrument_id=1, code='300866', period=PERIOD.isoformat(),
        unit='CNY', period_type='FY' if f in FLOW_FIELDS else 'instant', statement_scope='provider_default',
        source='tdx', source_record_id=1, source_filing_id=2, artifact_sha256='fixture',
        available_at='2026-04-30T16:00:00+00:00', value=v) for f, v in values.items()]


def test_arithmetic_does_not_approve_economic_scope():
    result = derive(facts(), 1, PERIOD, CUTOFF)
    assert result['calculations']['reported_ebitda_less_ebit']['value_cny'] == '30'
    assert result['ebitda_difference_less_core_components_cny'] == '0'
    assert result['calculations']['accounting_working_capital']['value_cny'] == '-100'
    assert result['calculations']['cashflow_working_capital_contribution']['value_cny'] == '-80'
    assert result['calculations']['noncash_working_capital_candidate']['value_cny'] == '-100'
    assert set(result['valuation_inputs'].values()) == {None}
    assert result['valuation_approved'] is False
    assert result['context']['right_of_use_depreciation'] == '10'  # not silently added to overlapping core


def test_missing_and_conflict_never_become_zero():
    rows = [r for r in facts() if r['field'] != 'short_term_borrowings']
    result = derive(rows, 1, PERIOD, CUTOFF)
    wc = result['calculations']['noncash_working_capital_candidate']
    assert wc['value_cny'] is None and wc['missing_fields'] == ['short_term_borrowings']
    assert all(r['value_cny'] is None for r in derive(facts(), 1, PERIOD, CUTOFF, True)['calculations'].values())
    assert all(r['value_cny'] is None for r in derive([], 1, PERIOD, CUTOFF)['calculations'].values())


@pytest.mark.parametrize('key,value', [('unit','million_CNY'), ('period_type','Q4'),
    ('instrument_id',2), ('statement_scope','parent'), ('source','pdf'),
    ('available_at','2027-01-01T00:00:00+00:00'), ('value','NaN'), ('source_record_id',0)])
def test_tampered_standard_fact_rejected(key, value):
    rows = facts()
    rows[0][key] = value
    with pytest.raises(ValueError):
        derive(rows, 1, PERIOD, CUTOFF)


def test_duplicate_and_mixed_source_records():
    rows = facts()
    with pytest.raises(ValueError, match='duplicate'):
        derive(rows+[deepcopy(rows[0])], 1, PERIOD, CUTOFF)
    for row in rows:
        if row['field'] == 'reported_ebitda':
            row['source_record_id'] = 3
    result = derive(rows, 1, PERIOD, CUTOFF)
    assert result['calculations']['reported_ebitda_less_ebit']['status'] == 'mixed_source_records'
    assert result['ebitda_difference_less_core_components_cny'] is None


def test_decimal_source_precision_is_not_repaired():
    rows = facts()
    amounts = {'reported_ebitda':'10.125', 'reported_ebit':'4.0625'}
    for row in rows:
        if row['field'] in amounts:
            row['value'] = amounts[row['field']]
    result = derive(rows, 1, PERIOD, CUTOFF)
    assert Decimal(result['calculations']['reported_ebitda_less_ebit']['value_cny']) == Decimal('6.0625')
