from copy import deepcopy
import sqlite3
from types import SimpleNamespace

import pytest
from tools import audit_reference_coverage as tool


def test_reference_audit_keeps_missing_country_conflicts_and_candidate_boundaries(monkeypatch):
    conn = sqlite3.connect(':memory:')
    conn.executescript('CREATE TABLE metadata(key TEXT,value TEXT); CREATE TABLE reference_company(release_id INTEGER,ticker TEXT,industry TEXT,country TEXT,source_locator TEXT);')
    conn.executemany('INSERT INTO metadata VALUES(?,?)', [('contract',tool.CONTRACT),('source_database_sha256','new-financial-data')])
    conn.executemany('INSERT INTO reference_company VALUES(?,?,?,?,?)', [
        (1,'SZSE:000553','Chemical (Specialty)','Israel','row2'),
        (1,'SZSE:000415','Retail (Distributors)','China','row3')])
    def company(code, industry=None, node='X2701', name='化工'):
        members = [dict(source='tdx',taxonomy_code='tdx_shenwan_industry',node_code=node,node_name=name)]
        if industry:
            members.append(dict(source='damodaran',taxonomy_code='damodaran_industry_2026',
                source_release_id=1,source_ticker='SZSE:'+code,node_name=industry,
                source_locator='row2' if code=='000553' else 'row3',artifact_sha256='hash'))
        return dict(code=code,ticker='SZSE:'+code,name=code,industry_evidence=members)
    coverage = dict(contract_version='alphalake-native-coverage-v1', full_source_universe=True,
        source_universe=3,source_database_sha256='old-financial-data',companies=[
            company('000553','Chemical (Specialty)'),company('000415','Retail (Distributors)','X510305','租赁'),company('003816')])
    store = SimpleNamespace(reference_snapshot=dict(id='snapshot',information_as_of='cutoff',releases=[dict(release_id=1,sha256='hash')]))
    monkeypatch.setattr(tool,'load_snapshot',lambda _:store)
    monkeypatch.setattr(tool,'reference_gaps',lambda _,ticker:
        [dict(field='country_erp_and_tax')] if ticker=='SZSE:000553' else
        [dict(field='industry'),dict(field='country_erp_and_tax')] if ticker=='SZSE:003816' else [])
    raw = dict(sha256='hash', companies=[
        dict(ticker='SZSE:000553',industry='Chemical (Specialty)',country='Israel',source_locator='row2'),
        dict(ticker='SZSE:000415',industry='Retail (Distributors)',country='China',source_locator='row3')])
    result = tool.audit(conn,coverage,raw)
    assert result['association_counts']=={'exact_source_security':2,'missing_exact_source_security':1}
    assert result['reference_gap_counts']=={'country_erp_and_tax':2,'industry':1}
    assert result['scope_counts']=={'nonfinancial_by_reference':1,'unresolved_industry_scope':2}
    assert result['scope_and_reference_complete']==0 and result['reference_complete']==1
    assert result['unresolved_companies'][2]['peer_industry_counts']=={'Chemical (Specialty)':1}
    categories = {c['node_code']: c for c in result['industry_categories']}
    assert categories['X2701']['priority_single_candidate_tickers']==['SZSE:003816']
    assert categories['X2701']['peer_industry_counts']=={'Chemical (Specialty)':1}
    assert [r['ticker'] for r in categories['X2701']['members']]==['SZSE:000553','SZSE:003816']
    assert categories['X510305']['peer_industry_counts']=={}
    assert categories['X510305']['status']=='candidate_not_approved'
    assert not result['defaults_changed'] and result['valuations_run']==0
    assert result['coverage_source_database_sha256'] != result['snapshot_source_database_sha256']
    tampered = deepcopy(coverage)
    tampered['companies'][0]['industry_evidence'][1]['artifact_sha256']='changed'
    with pytest.raises(ValueError,match='lineage mismatch'):
        tool.audit(conn,tampered,raw)
    with pytest.raises(ValueError,match='unique security denominator'):
        tool.audit(conn,coverage | {'companies':coverage['companies']+[coverage['companies'][0]],'source_universe':4},raw)
    altered = deepcopy(raw)
    altered['companies'][0]['country']='China'
    with pytest.raises(ValueError,match='workbook/published reference mismatch'):
        tool.audit(conn,coverage,altered)
    conn.close()
