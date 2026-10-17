from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import gzip
import sys
from unittest.mock import patch

import pytest

from tools.derive_tdx_capital import audit_export, derive, main, FIELDS, FLOW_FIELDS

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
    scope = result['depreciation_scope_review']
    assert scope['status'] == 'core_components_reconciled_scope_unverified'
    assert scope['arithmetic_identity']['status'] == 'within_source_rounding'
    assert not scope['valuation_approved']
    wc = result['working_capital_scope_review']
    assert wc['cashflow_contribution_cny'] == '-80'
    assert not wc['cashflow_contribution_is_change_in_noncash_wc']
    assert 'current_portion_noncurrent_liabilities' in wc['liability_classification_fields']


def test_missing_and_conflict_never_become_zero():
    rows = [r for r in facts() if r['field'] != 'short_term_borrowings']
    result = derive(rows, 1, PERIOD, CUTOFF)
    wc = result['calculations']['noncash_working_capital_candidate']
    assert wc['value_cny'] is None and wc['missing_fields'] == ['short_term_borrowings']
    assert all(r['value_cny'] is None for r in derive(facts(), 1, PERIOD, CUTOFF, True)['calculations'].values())
    assert all(r['value_cny'] is None for r in derive([], 1, PERIOD, CUTOFF)['calculations'].values())
    assert derive([],1,PERIOD,CUTOFF)['depreciation_scope_review']['status']=='missing_components_or_difference'
    assert derive(facts(),1,PERIOD,CUTOFF,True)['depreciation_scope_review']['status']=='source_record_conflict'


@pytest.mark.parametrize('component', ['right_of_use_depreciation', 'investment_property_depreciation_amortization'])
def test_zero_ebitda_difference_does_not_erase_known_depreciation(component):
    excluded = {'depreciation_depletion', 'intangible_amortization', 'deferred_expense_amortization',
        'right_of_use_depreciation', 'investment_property_depreciation_amortization'} - {component}
    rows = [r for r in facts() if r['field'] not in excluded]
    for row in rows:
        if row['field']=='reported_ebitda':row['value']='100'
    result = derive(rows,1,PERIOD,CUTOFF)
    review = result['depreciation_scope_review']
    assert review['status']=='zero_difference_with_positive_depreciation'
    assert review['positive_standard_components']==[component]
    assert result['valuation_inputs']['d_a'] is None


def test_partial_combined_presentation_is_not_inferred_from_arithmetic_match():
    rows = [r for r in facts() if r['field'] not in ('intangible_amortization','deferred_expense_amortization')]
    for row in rows:
        if row['field']=='depreciation_depletion':row['value']='30'
    result=derive(rows,1,PERIOD,CUTOFF)
    review=result['depreciation_scope_review']
    assert review['status']=='missing_components_or_difference'
    assert review['missing_comparison_fields']==['intangible_amortization','deferred_expense_amortization']
    assert result['valuation_inputs']['d_a'] is None


@pytest.mark.parametrize('changed_source', [False,True])
def test_depreciation_scope_keeps_unexplained_gap_and_mixed_lease_source(changed_source):
    rows=facts()
    for row in rows:
        if changed_source and row['field']=='right_of_use_depreciation':row['source_record_id']=3
        elif not changed_source and row['field']=='reported_ebitda':row['value']='200'
    review=derive(rows,1,PERIOD,CUTOFF)['depreciation_scope_review']
    assert review['status']==('source_record_conflict' if changed_source else 'reported_difference_has_unexplained_components')
    if not changed_source:
        assert review['arithmetic_identity']['residual_cny']=='70'
        assert review['arithmetic_identity']['status']=='outside_source_rounding'
    assert not review['valuation_approved']


@pytest.mark.parametrize('key,value', [('unit','million_CNY'), ('period_type','Q4'),
    ('instrument_id',2), ('statement_scope','parent'), ('source','pdf'),
    ('available_at','2027-01-01T00:00:00'), ('value','NaN'), ('source_record_id',0)])
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


def test_current_amounts_do_not_require_announcement_date_or_cninfo_link():
    for available in (None, '2027-01-01T00:00:00+00:00'):
        rows = facts()
        for row in rows:
            row.update(available_at=available, source_filing_id=None)
        result = derive(rows, 1, PERIOD, CUTOFF)
        assert result['calculations']['reported_ebitda_less_ebit']['value_cny'] == '30'
        evidence = result['source_evidence']
        assert evidence['financial_time_basis'] == 'current_standard_snapshot_not_pit'
        assert len(evidence['records']) == 1
        assert evidence['records'][0]['available_at'] == available
        assert evidence['records'][0]['source_filing_id'] is None
        assert evidence['field_values']['reported_ebit']['record_index'] == 0


def wide_export(data):
    data.mkdir()
    companies = [dict(instrument_id=1, exchange_mic='XSHE', symbols=['sz300866'],symbol_count=1,identifier_count=1)]
    definitions = [dict(field=f,provider_field='FN'+str(i+1),unit='CNY',period_basis='ytd' if f in FLOW_FIELDS else 'instant',
        valid_from='1900-01-01',valid_to=None) for i,f in enumerate(FIELDS)]
    row = dict(instrument_id=1,code='300866',period=PERIOD.isoformat(),source_record_id=1,
        source_filing_id=None,available_at=None,announcement_source='unknown',source_observed_at='2026-09-27',artifact_sha256='fixture')
    row.update({f['field']:f['value'] for f in facts()})
    for name, rows in [('companies',companies),('fields',definitions),('records',[row]),('conflicts',[])]:
        (data/(name+'.jsonl')).write_text(''.join(json.dumps(r)+'\n' for r in rows))


def test_actual_wide_contract_preserves_missing_positions_and_streams_source(tmp_path):
    wide_export(tmp_path/'data')
    rows = list(audit_export(tmp_path/'data', ['999999','300866'], [PERIOD, date(2024,12,31)], CUTOFF))
    assert len(rows) == 4
    assert rows[0]['calculations']['reported_ebitda_less_ebit']['status'] == 'missing_standard_fact'
    assert rows[1]['calculations']['reported_ebitda_less_ebit']['value_cny'] == '30'
    assert rows[2]['status'] == rows[3]['status'] == 'blocked_security_identity'
    assert len(rows[1]['source_evidence']['records']) == 1
    row = json.loads((tmp_path/'data/records.jsonl').read_text())
    row['code'] = '000001'
    (tmp_path/'data/records.jsonl').write_text(json.dumps(row)+'\n')
    with pytest.raises(ValueError, match='unexpected exported company'):
        list(audit_export(tmp_path/'data', ['300866'], [PERIOD], CUTOFF))


def test_cli_writes_current_contract_and_removes_failed_partial_output(tmp_path):
    database = tmp_path/'db'; database.write_bytes(b'unchanged-source')
    output = tmp_path/'result.json'
    args = ['derive_tdx_capital','--database',str(database),'--output',str(output),'--codes','300866',
        '--period',PERIOD.isoformat(),'--as-of',CUTOFF.isoformat()]
    def export(command, **kwargs):
        data = next(command[i+1] for i,v in enumerate(command) if v=='--output')
        from pathlib import Path
        wide_export(Path(data))
    with patch.object(sys,'argv',args),patch('tools.derive_tdx_capital.subprocess.run',side_effect=export):
        main()
    result = json.loads(output.read_text())
    assert result['contract'] == 'tdx-capital-derivation-v2' and result['positions'] == 1
    assert result['calculation_complete_counts']['reported_ebitda_less_ebit'] == 1
    assert result['results'][0]['calculations']['reported_ebitda_less_ebit']['value_cny'] == '30'
    compressed = tmp_path/'result.json.gz'; gzip_args = [str(compressed) if a==str(output) else a for a in args]
    with patch.object(sys,'argv',gzip_args),patch('tools.derive_tdx_capital.subprocess.run',side_effect=export):
        main()
    with gzip.open(compressed,'rt') as stream:
        assert json.load(stream)['results'] == result['results']
    output.unlink()
    with patch.object(sys,'argv',args),patch('tools.derive_tdx_capital.subprocess.run',side_effect=export),patch(
            'tools.derive_tdx_capital.derive',side_effect=ValueError('bad scope')):
        with pytest.raises(ValueError, match='bad scope'):
            main()
    assert not output.exists()
