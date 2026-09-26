import json
import sqlite3
from types import SimpleNamespace

import pytest
from tools.check_native_sqlite import check


def test_failed_diagnostic_keeps_company_and_continues_batch(tmp_path, monkeypatch):
    database = tmp_path/'input.sqlite'
    with sqlite3.connect(database) as conn:
        conn.executescript("CREATE TABLE companies(ticker TEXT); INSERT INTO companies VALUES('A'),('B');"
            "CREATE TABLE export_cells(ticker TEXT,series TEXT,field TEXT,status TEXT);"
            "INSERT INTO export_cells VALUES('A','company','mv_equity_listing','missing_eligible_close'),"
            "('B','company','mv_equity_listing','missing_eligible_close');")
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
