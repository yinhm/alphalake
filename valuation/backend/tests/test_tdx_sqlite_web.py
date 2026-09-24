"""真实Go标准链夹具 → SQLite → 网页API；政策独立，缺项及篡改拒绝。"""
import copy
from datetime import date, datetime
import json
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient

from api.main import app
from tools.export_alphalake_sqlite import export_snapshot
from tests.test_alphalake_integration import exports, REPO  # noqa: F401


def policy(code='300866'):
    source = json.loads((REPO/'valuation/examples/nonfinancial-baseline-2026H1-pilot.json').read_text())
    return dict(code=code, policy=source['assignments']['300866']['policy'])


def snapshot(path, payload):
    period, asof = date.fromisoformat(payload['report_period']), datetime.fromisoformat(payload['information_as_of'])
    instrument = payload['facts'][0]['instrument_id']
    code = payload['code']
    exchange, prefix = ('XSHE', 'sz') if code == '300866' else ('XSHG', 'sh')
    company = dict(symbols=[prefix+code], symbol_count=1, identifier_count=1,
                   exchange_mic=exchange, instrument_id=instrument, name='真 实 链 样 本')
    def fetch(code, end):
        data = copy.deepcopy(payload)
        data['report_period'] = end.isoformat()
        data['facts'] = [r for r in data['facts'] if end.year-1 <= int(r['period'][:4]) and r['period'] <= end.isoformat()]
        return data
    def statements(code, end):
        # 三表JSON只作为本测试展示夹具；真实主库验收另行查询Go financial-statements。
        return dict(contract_version='alphalake-financial-statements-v1', code=code,
                    report_period=end.isoformat(), information_as_of=asof.isoformat(), statements={})
    with sqlite3.connect(path) as conn:
        export_snapshot(conn, [company], fetch, period, asof, years=1, quarters=1, fetch_statements=statements)
        conn.executemany('INSERT INTO metadata VALUES(?,?)', [('report_period', period.isoformat()),
            ('information_as_of', asof.isoformat()), ('source_database_sha256', 'fixture')])


def test_original_request_reports_real_blockers(exports, tmp_path, monkeypatch):
    path = tmp_path/'financial.sqlite'
    snapshot(path, exports['300866'])
    monkeypatch.setenv('US_CN_HK_DB_PATH', str(path))
    def unexpected():
        raise AssertionError('incomplete data must not reach defaults or a model')
    monkeypatch.setattr('api.routes._get_damodaran_store', unexpected)
    monkeypatch.setattr('api.routes._get_industry_mapper', unexpected)
    before = path.read_bytes()
    with TestClient(app) as client:
        assert client.get('/api/search', params={'q':'真实链样本'}).json()['results'][0]['exchange_ticker']=='SZSE:300866'
        existence = client.get('/api/database/company-exists/SZSE:300866').json()
        assert existence['in_database'] and 'requires_tdx_policy' not in existence
        diagnostic = client.get('/api/database/compatibility/SZSE:300866').json()
        assert diagnostic['status']=='blocked_native_contract'
        assert any(r['field']=='ebit' for r in diagnostic['required_missing'])
        assert len(diagnostic['financial_fields'])==34
        request = {'ticker':'SZSE:300866', 'risk_free_rate':.0425}
        response = client.post('/api/valuation/from-database', json=request)
        assert response.status_code==422 and 'EBIT' in response.json()['detail']
        assert '政策' not in response.json()['detail']
        assert client.post('/api/valuation/from-database', json=dict(request, tdx_policy=policy())).status_code==422
        assert client.get('/api/database/compatibility/absent').status_code==404
    assert path.read_bytes()==before
    # 人工把缺列填成数字仍不能取得语义审核资格。
    with sqlite3.connect(path) as conn:
        for table in ('financials_annual', 'financials_quarterly'):
            conn.execute('UPDATE '+table+' SET revenues=1, ebit=1')
    with TestClient(app) as client:
        diagnostic = client.get('/api/database/compatibility/SZSE:300866').json()
        assert diagnostic['required_missing']==[]
        assert diagnostic['status']=='blocked_native_contract'
        assert client.post('/api/valuation/from-database', json=request).status_code==422


def test_missing_snapshot_contract_never_falls_back(exports, tmp_path, monkeypatch):
    path = tmp_path/'financial.sqlite'
    snapshot(path, exports['300866'])
    monkeypatch.setenv('US_CN_HK_DB_PATH', str(path))
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE metadata SET value='unsupported' WHERE key='contract'")
    with TestClient(app) as client:
        assert client.get('/api/database/company-exists/SZSE:300866').status_code==503
