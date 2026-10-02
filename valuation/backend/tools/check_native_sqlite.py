"""逐公司调用原生估值API；可携带显式参考政策，保留默认诊断及条件请求结果。"""
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


def check(database, output, risk_free_rate, review_methodology=False, reference_policy=None):
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
    requests = {}
    if reference_policy is not None:
        from api.database import FromDatabaseRequest
        if (not isinstance(reference_policy,dict) or set(reference_policy) != {'version','approval','evidence','requests'}
                or reference_policy['version'] != 'explicit-reference-policy-v1'
                or not isinstance(reference_policy['approval'], str) or not reference_policy['approval'].strip()
                or not isinstance(reference_policy['evidence'], str) or not reference_policy['evidence'].strip()
                or not isinstance(reference_policy['requests'], list) or not reference_policy['requests']):
            raise ValueError('explicit reference policy approval/evidence/requests required')
        for item in reference_policy['requests']:
            if (not isinstance(item,dict) or set(item) != {'ticker','industry_override','country_override'}
                    or any(not isinstance(v,str) or not v.strip() for v in item.values())):
                raise ValueError('explicit ticker/industry/country required; no other overrides')
            request = FromDatabaseRequest.model_validate(item | {'risk_free_rate':risk_free_rate}).model_dump()
            if request['ticker'] not in tickers or request['ticker'] in requests:
                raise ValueError('unknown or duplicate reference policy ticker')
            requests[request['ticker']] = request
    results, gaps = [], Counter()
    with TestClient(app, raise_server_exceptions=False) as client:
        for ticker in tickers:
            diagnostic = client.get('/api/database/compatibility/'+ticker)
            d = diagnostic.json() if diagnostic.status_code == 200 else {
                'status':'diagnostic_error', 'http_status':diagnostic.status_code, 'response':diagnostic.text}
            request = requests.get(ticker, {'ticker':ticker, 'risk_free_rate':risk_free_rate})
            response = client.post('/api/valuation/from-database', json=request)
            try:
                body = response.json()
            except ValueError:
                body = {'unhandled_response':response.text}
            missing = sorted({r['field'] for group in ('required_missing','conditional_missing') for r in d.get(group, [])})
            gaps.update(missing)
            record = dict(ticker=ticker, admission=d['status'], financial_admission=d.get('financial_status',d['status']), reference_missing=d.get('reference_missing',[]), http_status=response.status_code,
                          market_status=market.get(ticker, 'missing_market_diagnostic'), missing_fields=missing,
                          final=body.get('final'), diagnostic=d, response=body,
                          diagnostic_sha256=value_digest(d), inputs_sha256=value_digest(body.get('inputs')),
                          unresolved_sha256=value_digest(body.get('unresolved_fields')))
            if ticker in requests:
                record.update(request=request, reference_policy_sha256=value_digest(reference_policy),
                              request_status='calculated_conditional_reference_proxy' if response.status_code == 200 else 'rejected',
                              policy_approval=reference_policy['approval'], policy_evidence=reference_policy['evidence'])
            if review_methodology:
                from tools.review_native_policy import review_report
                record['methodology_review'] = (review_report(body) if response.status_code == 200 else
                    dict(status='not_calculated', reason=d['status'], automatic_adoption=False))
            (output/(ticker.replace(':','-')+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
            item = {k:record[k] for k in ('ticker','admission','financial_admission','reference_missing','http_status','market_status','missing_fields','final','diagnostic_sha256','inputs_sha256','unresolved_sha256')}
            if review_methodology:
                item['methodology'] = record['methodology_review'].get('methodology', record['methodology_review'])
            results.append(item)
    with database.open('rb') as stream:
        after = hashlib.file_digest(stream,'sha256').hexdigest()
    assert before == after, 'valuation must not mutate source SQLite'
    summary = dict(database=str(database), sha256=before, companies=len(tickers), risk_free_rate=risk_free_rate,
                   scope='native_API_with_existing_policies_and_editable_defaults_not_valuation_accuracy',
                   market_status=dict(Counter(r['market_status'] for r in results)),
                   admission=dict(Counter(r['admission'] for r in results)),
                   financial_admission=dict(Counter(r['financial_admission'] for r in results)),
                   http_status=dict(Counter(r['http_status'] for r in results)),
                   missing_company_counts=dict(gaps), results=results)
    if review_methodology:
        summary['methodology'] = dict(companies=len(results),
            reviewed=sum('version' in r['methodology'] for r in results),
            not_calculated=sum('version' not in r['methodology'] for r in results),
            findings=dict(Counter(f for r in results for f in r['methodology'].get('findings', []))),
            economic_approval='not_inferred', predictive_validity='not_established')
    if reference_policy is not None:
        summary['explicit_reference_policy'] = dict(sha256=value_digest(reference_policy),
            approval=reference_policy['approval'], evidence=reference_policy['evidence'],
            requests=len(requests), default_admission_unchanged=True, automatic_mapping=False,
            economic_approval='not_inferred', results=[dict(ticker=r['ticker'], http_status=r['http_status'])
                for r in results if r['ticker'] in requests])
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    assert all(r['admission']!='diagnostic_error' for r in results), 'diagnostic failure; see summary'
    assert all(r['http_status'] in (200,422) for r in results), 'unexpected API failure; see summary'
    assert all(r['admission']=='ready' or r['http_status']==422 or (
        r['ticker'] in requests and r['financial_admission']=='ready') for r in results), 'financially blocked company valued'
    return {k:v for k,v in summary.items() if k!='results'}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--risk-free-rate',type=float,required=True,help='explicit native API assumption, not an observed market rate')
    parser.add_argument('--review-methodology',action='store_true',help='replay calculated reports and audit the five method dimensions; retains blocked companies')
    parser.add_argument('--reference-policy',type=Path,help='explicit approved industry/country requests; no default mapping or financial overrides')
    args=parser.parse_args()
    print(json.dumps(check(args.database,args.output,args.risk_free_rate,args.review_methodology,
        json.loads(args.reference_policy.read_text()) if args.reference_policy else None),ensure_ascii=False,indent=2))
