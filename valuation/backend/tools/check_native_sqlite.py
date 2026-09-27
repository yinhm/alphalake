"""逐公司调用原生估值API，保留成功、缺项和计算拒绝；不修改输入政策。"""
import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3


def value_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def check(database, output, risk_free_rate):
    from fastapi.testclient import TestClient
    from api.main import app

    if not math.isfinite(risk_free_rate):
        raise ValueError('finite risk-free rate required')
    os.environ['US_CN_HK_DB_PATH'] = str(database.resolve(strict=True))
    output.mkdir(parents=True, exist_ok=True)
    with database.open('rb') as stream:
        before = hashlib.file_digest(stream, 'sha256').hexdigest()
    with sqlite3.connect('file:'+str(database.resolve())+'?mode=ro', uri=True) as conn:
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        tickers = [r[0] for r in conn.execute('SELECT ticker FROM companies ORDER BY ticker')]
        market = dict(conn.execute("SELECT ticker,status FROM export_cells WHERE series='company' AND field='mv_equity_listing'"))
    results, gaps = [], Counter()
    with TestClient(app, raise_server_exceptions=False) as client:
        for ticker in tickers:
            diagnostic = client.get('/api/database/compatibility/'+ticker)
            d = diagnostic.json() if diagnostic.status_code == 200 else {
                'status':'diagnostic_error', 'http_status':diagnostic.status_code, 'response':diagnostic.text}
            response = client.post('/api/valuation/from-database', json={'ticker':ticker, 'risk_free_rate':risk_free_rate})
            try:
                body = response.json()
            except ValueError:
                body = {'unhandled_response':response.text}
            missing = sorted({r['field'] for group in ('required_missing','conditional_missing') for r in d.get(group, [])})
            gaps.update(missing)
            record = dict(ticker=ticker, admission=d['status'], http_status=response.status_code,
                          market_status=market.get(ticker, 'missing_market_diagnostic'), missing_fields=missing,
                          final=body.get('final'), diagnostic=d, response=body,
                          diagnostic_sha256=value_digest(d), inputs_sha256=value_digest(body.get('inputs')),
                          unresolved_sha256=value_digest(body.get('unresolved_fields')))
            (output/(ticker.replace(':','-')+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
            results.append({k:record[k] for k in ('ticker','admission','http_status','market_status','missing_fields','final','diagnostic_sha256','inputs_sha256','unresolved_sha256')})
    with database.open('rb') as stream:
        after = hashlib.file_digest(stream,'sha256').hexdigest()
    assert before == after, 'valuation must not mutate source SQLite'
    summary = dict(database=str(database), sha256=before, companies=len(tickers), risk_free_rate=risk_free_rate,
                   scope='native_API_with_existing_policies_and_editable_defaults_not_valuation_accuracy',
                   market_status=dict(Counter(r['market_status'] for r in results)),
                   admission=dict(Counter(r['admission'] for r in results)),
                   http_status=dict(Counter(r['http_status'] for r in results)),
                   missing_company_counts=dict(gaps), results=results)
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    assert all(r['admission']!='diagnostic_error' for r in results), 'diagnostic failure; see summary'
    assert all(r['http_status'] in (200,422) for r in results), 'unexpected API failure; see summary'
    assert all(r['admission']=='ready' or r['http_status']==422 for r in results), 'blocked company valued'
    return {k:v for k,v in summary.items() if k!='results'}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--risk-free-rate',type=float,required=True,help='explicit native API assumption, not an observed market rate')
    args=parser.parse_args()
    print(json.dumps(check(args.database,args.output,args.risk_free_rate),ensure_ascii=False,indent=2))
