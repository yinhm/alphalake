import json
import sqlite3

import pytest
from tools.check_native_sqlite import check


def test_explicit_reference_requests_preserve_default_diagnostic_and_financial_gate(tmp_path, monkeypatch):
    database = tmp_path/'input.sqlite'
    with sqlite3.connect(database) as conn:
        conn.executescript("CREATE TABLE companies(ticker TEXT); CREATE TABLE export_cells(ticker TEXT,status TEXT,series TEXT,field TEXT);")
        conn.executemany('INSERT INTO companies VALUES(?)', [('SZSE:001220',),('SZSE:001257',),('SZSE:300866',)])
    policy = dict(version='explicit-reference-policy-v1', approval='用户批准', evidence='审核文档及原文', requests=[
        dict(ticker='SZSE:001220',industry_override='Transportation',country_override='China'),
        dict(ticker='SZSE:001257',industry_override='Metals & Mining',country_override='China')])
    posted=[]
    class Response:
        def __init__(self, status, body): self.status_code,self.body,self.text=status,body,str(body)
        def json(self): return self.body
    class Client:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, path):
            ticker=path.rsplit('/',1)[1]
            return Response(200,dict(status='ready' if ticker.endswith('300866') else 'blocked_reference_inputs',
                financial_status='blocked_required_inputs' if ticker.endswith('001257') else 'ready',
                reference_missing=['industry'],required_missing=[],conditional_missing=[]))
        def post(self, path, json):
            posted.append(json)
            return Response(422 if json['ticker'].endswith('001257') else 200, {})
    monkeypatch.setattr('fastapi.testclient.TestClient',Client)
    result=check(database,tmp_path/'out',.0425,reference_policy=policy)
    assert result['http_status']=={200:2,422:1}
    assert result['admission']['blocked_reference_inputs']==2
    assert posted[0]['industry_override']=='Transportation' and 'industry_override' not in posted[2]
    record=json.loads((tmp_path/'out/SZSE-001220.json').read_text())
    assert record['admission']=='blocked_reference_inputs' and record['request_status']=='calculated_conditional_reference_proxy'
    for bad, message in [
        (policy | {'approval':''}, 'approval'),
        (policy | {'requests':policy['requests']*2}, 'duplicate'),
        (policy | {'requests':[policy['requests'][0] | {'ticker':'SZSE:999999'}]}, 'unknown'),
        (policy | {'requests':[policy['requests'][0] | {'risk_free_rate':.01}]}, 'other overrides'),
    ]:
        with pytest.raises(ValueError,match=message): check(database,tmp_path/'bad',.0425,reference_policy=bad)
    original=Client.post
    def invalid_success(self,path,json):
        return Response(200,{}) if json['ticker'].endswith('001257') else original(self,path,json)
    monkeypatch.setattr(Client,'post',invalid_success)
    with pytest.raises(AssertionError,match='financially blocked'):
        check(database,tmp_path/'invalid-success',.0425,reference_policy=policy)
