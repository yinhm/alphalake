import json
import sqlite3

import pytest
from tools import audit_native_coverage as coverage


def test_coverage_keeps_financial_unknown_and_missing_denominators(monkeypatch):
    conn = sqlite3.connect(':memory:'); conn.row_factory = sqlite3.Row
    conn.executescript('CREATE TABLE metadata(key TEXT,value TEXT); CREATE TABLE export_universe(candidate INTEGER,code TEXT,instrument_id INTEGER,status TEXT);')
    metadata = dict(contract=coverage.CONTRACT, report_period='2026-06-30', information_as_of='2026-09-27T00:00:00Z', source_database_sha256='source')
    conn.executemany('INSERT INTO metadata VALUES(?,?)', metadata.items())
    conn.executemany('INSERT INTO export_universe VALUES(?,?,?,?)', [(1,'600000',1,'exported'),(2,'600001',2,'exported'),(3,'600002',3,'blocked_security_identity')])
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
    result = coverage.audit(conn, ready)
    assert result['snapshot_candidates']==3 and result['full_source_universe']
    assert result['valuations_run']==0
    assert result['scope_counts']=={'outside_financial_scope':1,'nonfinancial_by_reference':1,'unresolved_industry_scope':1}
    assert result['missing_categories']=={'research_history':{'nonfinancial_by_reference':1}}
    assert len(result['companies'][1]['gaps'])==2
    assert result['companies'][2]['admission']=='blocked_security_identity'
    assert coverage.company_scope(companies[0], {'status':'not_published'})[0]=='unresolved_industry_scope'
    with pytest.raises(ValueError,match='cutoff mismatch'):
        coverage.audit(conn, ready | {'information_as_of':'2026-09-28T00:00:00Z'})
    with pytest.raises(ValueError,match='source identities'):
        coverage.audit(conn, ready | {'companies':companies[:2], 'universe_count':2})
    conn.close()


def test_financial_policy_uses_existing_catalogue_and_gap_is_not_root_cause():
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    packet = json.loads((root/'internal/source/damodaran/testdata/beta-expected.json').read_text())
    assert coverage.FINANCIAL_INDUSTRIES <= {r['industry'] for r in packet['observations']}
    assert coverage.gap_category(dict(series='quarterly',offset=0,field='r_and_d_expense'))=='research_ttm'
    assert coverage.gap_category(dict(series='annual',offset=0,field='r_and_d_expense'))=='research_base_year'


def test_tdx_financial_identity_is_used_but_conflict_is_not_guessed():
    member = dict(source='tdx',taxonomy_code='tdx_shenwan_industry',node_code='X5101',node_name='证券')
    company = dict(industry_memberships=[member])
    assert coverage.company_scope(company, {'status':'not_published'})[0]=='outside_financial_scope'
    company['industry_memberships'].append(dict(source='damodaran',taxonomy_code='damodaran_industry_2026',node_name='Retail (General)'))
    assert coverage.company_scope(company, {'status':'verified_source'})[0]=='unresolved_industry_scope'
    member['node_name']='tampered'
    assert coverage.company_scope(dict(industry_memberships=[member]), {})[0]=='unresolved_industry_scope'
