import json
import sqlite3
from types import SimpleNamespace

import pytest
from tools.check_native_sqlite import check


def test_delete_releases_only_requested_session(monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app
    from api import session_store
    other = object()
    monkeypatch.setattr(session_store, '_sessions', {'verification':object(), 'user':other})
    with TestClient(app) as client:
        assert client.delete('/api/valuation/verification').status_code == 204
        assert client.get('/api/valuation/verification').status_code == 404
        assert client.delete('/api/valuation/verification').status_code == 404
    assert session_store.list_sessions() == ['user']
    assert session_store.get_session('user') is other


def test_failed_diagnostic_keeps_company_and_continues_batch(tmp_path, monkeypatch):
    database = tmp_path/'input.sqlite'
    with sqlite3.connect(database) as conn:
        conn.executescript("CREATE TABLE companies(ticker TEXT); INSERT INTO companies VALUES('A'),('B'); CREATE TABLE metadata(key TEXT,value TEXT);")
        conn.executemany('INSERT INTO metadata VALUES(?,?)',[('input_context:'+t,json.dumps(dict(ticker=t,market=dict(status='missing_eligible_close')))) for t in ('A','B')])
    calls = []

    class Client:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, path):
            return SimpleNamespace(status_code=503 if path.endswith('/A') else 200,
                text='unavailable', json=lambda: dict(status='blocked_required_inputs', required_missing=[], conditional_missing=[]))
        def post(self, path, json):
            calls.append(json['ticker'])
            return SimpleNamespace(status_code=422, json=lambda: dict(detail='missing input'))

    monkeypatch.setenv('US_CN_HK_DB_PATH', str(database))
    monkeypatch.setattr('fastapi.testclient.TestClient', lambda *args, **kwargs: Client())
    output=tmp_path/'output'
    with pytest.raises(AssertionError, match='diagnostic failure'):
        check(database, output, .0425)
    summary=json.loads((output/'summary.json').read_text())
    assert calls == ['A','B'] and summary['companies']==2
    assert summary['admission']=={'diagnostic_error':1, 'blocked_required_inputs':1}


def test_economic_comparison_separates_only_protocol_and_source_references():
    from copy import deepcopy
    from tools.check_native_sqlite import economic_payload, value_digest
    old = dict(inputs=dict(prepared_ttm=dict(financials=dict(revenues=100),provenance=dict(
        basis='standard_cumulative_ttm',contract='old',source_database_sha256='old',components='full evidence',
        cumulative_windows=json.dumps(dict(current=dict(revenues=60),prior=dict(revenues=50))),
        prior_ttm_revenue=json.dumps(dict(period_end='2025-06-30',currency='CNY',money_unit='million_reporting_currency',
            revenues='90',formula='FY + current_YTD - prior_same_YTD',components=[dict(value='50')]))))),dcf=dict(pv_fcff=20))
    new = deepcopy(old)
    provenance = new['inputs']['prepared_ttm']['provenance']
    provenance.update(contract='new',source_database_sha256='new',components='references')
    prior = json.loads(provenance['prior_ttm_revenue']);prior.pop('components');prior['source_periods']=['2024-12-31']
    provenance['prior_ttm_revenue']=json.dumps(prior,sort_keys=True)
    assert value_digest(economic_payload(old))==value_digest(economic_payload(new))
    for key,value in [('revenues','91'),('currency','USD'),('period_end','2024-06-30')]:
        prior[key]=value;provenance['prior_ttm_revenue']=json.dumps(prior)
        assert value_digest(economic_payload(old))!=value_digest(economic_payload(new))
        prior=json.loads(old['inputs']['prepared_ttm']['provenance']['prior_ttm_revenue']);prior.pop('components')
    assert old['inputs']['prepared_ttm']['provenance']['contract']=='old'
