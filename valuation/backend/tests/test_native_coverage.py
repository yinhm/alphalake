from data_sources import valuation_scope
import json
import sqlite3

import pytest
from tools import audit_native_coverage as coverage


def test_coverage_keeps_financial_unknown_and_missing_denominators(monkeypatch):
    conn = sqlite3.connect(':memory:'); conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE metadata(key TEXT,value TEXT)')
    metadata = dict(contract=coverage.CONTRACT, report_period='2026-06-30', information_as_of='2026-09-27T00:00:00Z', source_database_sha256='source')
    conn.executemany('INSERT INTO metadata VALUES(?,?)', metadata.items())
    conn.executemany('INSERT INTO metadata VALUES(?,?)',[('input_context:SHSE:'+code,json.dumps(dict(ticker='SHSE:'+code,instrument_id=i)))
        for i,code in [(1,'600000'),(2,'600001')]])
    member = dict(source='damodaran',taxonomy_code='damodaran_industry_2026',node_name='Bank (Money Center)')
    companies = [dict(instrument_id=i, name=str(i), exchange_mic='XSHG', symbols=[f'sh{599999+i}'],
                     industry_memberships=[member if i==1 else member | {'node_name':'Retail (General)'}] if i<3 else []) for i in range(1,4)]
    ready = dict(contract_version='alphalake-readiness-v3', report_period=metadata['report_period'],
                 information_as_of=metadata['information_as_of'], universe_count=3, companies=companies,
                 company_industry_reference={'status':'verified_source'})
    def diagnostic(conn, ticker):
        return dict(status='ready' if ticker.endswith('600000') else 'blocked_required_inputs', blockers=[],
            required_missing=[], conditional_missing=[] if ticker.endswith('600000') else [
                dict(series='annual',offset=o,field='r_and_d_expense',period=f'{2025-o}-12-31',status='missing_standard_facts') for o in (1,2)],
            adjustment_selection={'rd':True}, optional_history_missing=[], market_proxy=None, exported_asset_proxies=[])
    monkeypatch.setattr(coverage.db, 'native_compatibility', diagnostic)
    scope = dict(contract='alphalake-export-scope-v1',source_database_sha256='source',report_period=metadata['report_period'],
        information_as_of=metadata['information_as_of'],companies=companies)
    result = coverage.audit(conn, ready, scope)
    assert result['snapshot_candidates']==3 and result['full_source_universe']
    assert result['valuations_run']==0
    assert result['scope_counts']=={'outside_financial_scope':1,'nonfinancial_by_reference':1,'unresolved_industry_scope':1}
    assert result['missing_categories']=={'research_history':{'nonfinancial_by_reference':1}}
    assert len(result['companies'][1]['gaps'])==2
    assert result['companies'][2]['admission']=='blocked_security_identity'
    assert valuation_scope.company_scope(companies[0], {'status':'not_published'})[0]=='unresolved_industry_scope'
    with pytest.raises(ValueError,match='cutoff mismatch'):
        coverage.audit(conn, ready | {'information_as_of':'2026-09-28T00:00:00Z'},scope)
    with pytest.raises(ValueError,match='source identities'):
        coverage.audit(conn, ready | {'companies':companies[:2], 'universe_count':2},scope)
    conn.close()


def test_financial_policy_uses_existing_catalogue_and_gap_is_not_root_cause():
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    packet = json.loads((root/'internal/source/damodaran/testdata/beta-expected.json').read_text())
    assert valuation_scope.FINANCIAL_INDUSTRIES <= {r['industry'] for r in packet['observations']}
    assert coverage.gap_category(dict(series='quarterly',offset=0,field='r_and_d_expense'))=='research_ttm'
    assert coverage.gap_category(dict(series='annual',offset=0,field='r_and_d_expense'))=='research_base_year'


def test_tdx_financial_identity_is_used_but_conflict_is_not_guessed():
    member = dict(source='tdx',taxonomy_code='tdx_shenwan_industry',node_code='X5101',node_name='证券')
    company = dict(industry_memberships=[member])
    assert valuation_scope.company_scope(company, {'status':'not_published'})[0]=='outside_financial_scope'
    company['industry_memberships'].append(dict(source='damodaran',taxonomy_code='damodaran_industry_2026',node_name='Retail (General)'))
    assert valuation_scope.company_scope(company, {'status':'verified_source'})[0]=='unresolved_industry_scope'
    member['node_name']='tampered'
    assert valuation_scope.company_scope(dict(industry_memberships=[member]), {})[0]=='unresolved_industry_scope'


def test_profit_asset_evidence_displays_components_without_normalizing_or_approving():
    from copy import deepcopy
    fields=[dict(canonical_field=name,value=str(value),unit='CNY',status='complete',scope='consolidated_statement')
        for name,value in [('interest_income',1000000),('investment_income',-2000000),
                           ('fair_value_change_income',0),('asset_disposal_income',0)]]
    company=dict(fields=fields)
    diagnostic=dict(input_contract=dict(flow_basis='standard_cumulative_TTM',inputs=[
        dict(series='ttm',offset=0,field=f,value=v,usable=True,source={'status':s}) for f,v,s in
        [('ebit',10,'available'),('cash_and_marketable_securities',3,'estimated_partial_scope'),
         ('cross_holdings',5,'estimated_partial_scope'),('minority_interests',0,'available')]]))
    before=deepcopy((company,diagnostic))
    result=coverage.profit_asset_evidence(company,diagnostic)
    assert result['status']=='components_available_scope_unverified'
    assert result['nonzero_components']==['interest_income','investment_income']
    assert [r['value_million_cny'] for r in result['components']]==['1','-2','0','0']
    assert [r['ratio_to_reported_ebit'] for r in result['components']][:2]==['0.1','-0.2']
    assert result['current_equity_bridge_inputs'][0]['source_status']=='estimated_partial_scope'
    assert not result['automatic_adjustment'] and not result['operating_scope_verified']
    assert (company,diagnostic)==before and 'normalized_ebit' not in result
    diagnostic['input_contract']['inputs'][0]['value']=-10
    negative=coverage.profit_asset_evidence(company,diagnostic)
    assert [r['ratio_to_reported_ebit'] for r in negative['components']][:2]==['-0.1','0.2']
    diagnostic['input_contract']['inputs'][0]['value']=10
    # Source zero, absence, incompatible unit, duplicate scope and zero denominator differ.
    for row in fields:row['value']='0'
    zero=coverage.profit_asset_evidence(company,diagnostic)
    assert zero['nonzero_components']==[] and not zero['operating_scope_verified']
    fields.pop()
    assert coverage.profit_asset_evidence(company,diagnostic)['components'][-1]['value_million_cny'] is None
    fields[0]['unit']='USD'
    assert coverage.profit_asset_evidence(company,diagnostic)['components'][0]['value_million_cny'] is None
    fields.append(deepcopy(fields[1]))
    assert coverage.profit_asset_evidence(company,diagnostic)['components'][1]['status']=='ambiguous_component_scope'
    diagnostic['input_contract']['inputs'][0]['value']=0
    assert all(r['ratio_to_reported_ebit'] is None for r in coverage.profit_asset_evidence(company,diagnostic)['components'])
    company['source_conflicts']=[{'period':'2026-06-30'}]
    assert coverage.profit_asset_evidence(company,diagnostic)['status']=='source_conflict_requires_review'
