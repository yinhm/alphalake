"""真实Go标准链夹具 → SQLite → 网页API；政策独立，缺项及篡改拒绝。"""
import copy
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import pytest

from api.main import app
from api.alphalake import evaluate
from data_sources.alphalake import AlphaLakeRequest
from data_sources import us_cn_hk_db as db
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


def test_web_roundtrip_and_rejections(exports, tmp_path, monkeypatch):
    path = tmp_path/'financial.sqlite'
    snapshot(path, exports['300866'])
    monkeypatch.setenv('US_CN_HK_DB_PATH', str(path))
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR', str(tmp_path/'runs'))
    # TDX模式不触发任何外部行业或宏观默认读取。
    def unexpected():
        raise AssertionError('external defaults must not be read')
    monkeypatch.setattr('api.routes._get_damodaran_store', unexpected)
    monkeypatch.setattr('api.routes._get_industry_mapper', unexpected)
    before = path.read_bytes()
    with TestClient(app) as client:
        assert client.get('/api/search', params={'q':'300866'}).json()['results'][0]['exchange_ticker']=='SZSE:300866'
        assert client.get('/api/search', params={'q':'missing'}).json()['results']==[]
        assert client.get('/api/search', params={'q':'真实链样本'}).json()['results'][0]['exchange_ticker']=='SZSE:300866'
        assert client.get('/api/database/company-exists/SZSE:300866').json()['requires_tdx_policy']
        assert client.get('/api/database/company/SZSE:300866').json()['standard_financials']
        request = dict(ticker='SZSE:300866', tdx_policy=policy())
        missing_policy = client.post('/api/valuation/from-database', json={'ticker': request['ticker']})
        assert missing_policy.status_code==422 and missing_policy.json()['detail']['status']=='explicit_policy_required'
        result = client.post('/api/valuation/from-database', json=request)
        assert result.status_code==200, result.text
        web = result.json()
        with db.get_connection() as conn:
            native = evaluate(AlphaLakeRequest(data=db.fetch_tdx_valuation(conn, request['ticker']), policy=policy()['policy']))
            with pytest.raises(sqlite3.OperationalError, match='readonly'):
                conn.execute('DELETE FROM companies')
        assert web['alphalake']['run_id']==native['run_id']
        for part in ['dcf','final','cost_of_capital']:
            assert web[part]==native['report'][part]
        assert client.get('/api/valuation/'+web['id']).json()['alphalake']==web['alphalake']
        assert client.patch('/api/valuation/'+web['id'], json={'overrides':{'macro_inputs.tax_rate_marginal':0}}).status_code==409
        for mutation in ('code','period','wacc','external'):
            bad = copy.deepcopy(request)
            if mutation=='code': bad['tdx_policy']['code']='600519'
            elif mutation=='period': bad['tdx_policy']['policy']['approved_report_period']='2025-12-31'
            elif mutation=='wacc': bad['tdx_policy']['policy']['wacc']=None
            else: bad['risk_free_rate']=.0425
            assert client.post('/api/valuation/from-database', json=bad).status_code==422
        assert path.read_bytes()==before
        # 数值篡改即使重算SQLite载荷哈希，也必须被源位/窗口验证拒绝。
        with sqlite3.connect(path) as conn:
            raw = json.loads(conn.execute('SELECT payload_json FROM valuation_inputs').fetchone()[0])
            next(r for r in raw['facts'] if r['field']=='operating_profit_cumulative')['value']='1'
            serialized=json.dumps(raw)
            conn.execute('UPDATE valuation_inputs SET payload_json=?,sha256=?', (serialized,hashlib.sha256(serialized.encode()).hexdigest()))
        assert client.post('/api/valuation/from-database', json=request).status_code==422
        with sqlite3.connect(path) as conn:
            conn.execute("UPDATE metadata SET value='alphalake-compatible-sqlite-v1' WHERE key='contract'")
        assert client.get('/api/database/company-exists/SZSE:300866').status_code==503


def test_financial_operations_and_missing_not_defaulted(exports, tmp_path, monkeypatch):
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR', str(tmp_path/'runs'))
    for code in ('600519','300866'):
        payload=copy.deepcopy(exports[code])
        if code=='300866':
            payload['windows']=[r for r in payload['windows'] if r['field']!='interest_expense']
        path=tmp_path/(code+'.sqlite')
        snapshot(path,payload)
        monkeypatch.setenv('US_CN_HK_DB_PATH',str(path))
        with TestClient(app) as client:
            result=client.post('/api/valuation/from-database',json=dict(ticker=('SHSE:' if code=='600519' else 'SZSE:')+code,tdx_policy=policy(code)))
            assert result.status_code==422, result.text
            if code=='600519': assert 'financial operations' in result.text
            else: assert result.json()['detail']['missing']==['TDX/interest_expense']
    assert not (tmp_path/'runs').exists()
