"""只读冻结预测，在固定截止后核验实际；不重新拟合或生成预测。"""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path

from tools.backtest_tdx_history import at, error
from tools.backtest_tdx_multiyear_growth import actual_operating
from tools.backtest_tdx_origins import summarize, gates


def load_frozen(directory):
    receipt=json.loads((directory/'forward-receipt.json').read_bytes())
    contents={}
    for name in ('forward-plan.json','forward-2026H1.json.gz'):
        raw=(directory/name).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=receipt['files'][name]:raise ValueError('frozen file hash differs: '+name)
        contents[name]=raw
    plan=json.loads(contents['forward-plan.json'])
    saved=json.loads(gzip.decompress(contents['forward-2026H1.json.gz']))
    parent_raw=Path(plan['parent_path']).read_bytes()
    if hashlib.sha256(parent_raw).hexdigest()!=plan['parent_sha256']:raise ValueError('parent policy hash differs')
    parent=json.loads(parent_raw)
    expected=[s['code'] for s in parent['samples'] if s['split']=='holdout']
    if ([r['code'] for r in saved['rows']]!=expected or saved['positions']!=len(expected)
            or len(set(expected))!=120 or saved['validation_gates']!=parent['validation']):
        raise ValueError('frozen sample or validation gates differ')
    return plan,saved,receipt['files']


def review(plan,saved,load_actual,now):
    for key in ('origin','target','forecast_cutoff','evaluation_cutoff'):
        if saved[key]!=plan[key]:raise ValueError('frozen timing differs')
    if at(now)<at(saved['prepared_at']):raise ValueError('review precedes saved forecast')
    rows=deepcopy(saved['rows'])
    output=dict(study_id=plan['study_id'],reviewed_at=now,target=plan['target'],
        evaluation_cutoff=plan['evaluation_cutoff'],positions=len(rows),rows=rows,
        status='awaiting_evaluation_cutoff',decision=None,
        boundary='frozen predictions; retained input rejections; no refit, production adoption or full DCF validation')
    if at(now)<at(plan['evaluation_cutoff']):
        output['statuses']=dict(Counter(r['status'] for r in rows))
        return output
    source=load_actual()
    if source['contract_version']!='tdx-history-source-v1':raise ValueError('actual source contract differs')
    artifacts={a['file']:a for a in source['artifacts']}
    if len(artifacts)!=len(source['artifacts']):raise ValueError('duplicate actual artifact')
    index=defaultdict(list)
    for row in source['records']:index[row['code'],row['period']].append(row)
    for row in rows:
        row['origin']=plan['origin']
        if row['status']=='blocked_inputs':continue
        if row['status']!='awaiting_target':raise ValueError('unexpected frozen row status')
        row['status']='blocked_actual'
        try:
            actual=actual_operating(index,artifacts,row['code'],date.fromisoformat(plan['target']),plan['evaluation_cutoff'])
            row.update(status='evaluated',actual=actual,errors={m:error(row['forecasts'][m],actual) for m in plan['models']})
        except (ValueError,KeyError,ArithmeticError) as exc:row['reason']=str(exc)
    summary=summarize(rows,plan['models'])
    summary.pop('by_profit_scope')  # 本入口未逐公司核验金融兼营范围，不伪造分组。
    output.update(status='evaluated_at_fixed_cutoff',statuses=dict(Counter(r['status'] for r in rows)),
        summary=summary,decision=gates(summary,'bias_half',saved['validation_gates'],rows),actual_snapshot=source)
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    parser.add_argument('actual_snapshot',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    plan,saved,hashes=load_frozen(args.directory)
    actual_digest=None
    def load_actual():
        global actual_digest
        raw=args.actual_snapshot.read_bytes()
        actual_digest=hashlib.sha256(raw).hexdigest()
        return json.loads(gzip.decompress(raw) if args.actual_snapshot.suffix=='.gz' else raw)
    result=review(plan,saved,load_actual,datetime.now(timezone.utc).isoformat())
    result['evidence']=dict(frozen_files=hashes,actual_file_sha256=actual_digest,
        review_code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        helpers={name:hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in (
            'valuation/backend/tools/backtest_tdx_history.py','valuation/backend/tools/backtest_tdx_multiyear_growth.py','valuation/backend/tools/backtest_tdx_origins.py')})
    raw=json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False).encode()+b'\n'
    with args.output.open('xb') as f:f.write(gzip.compress(raw,mtime=0))
    print(json.dumps({k:result[k] for k in ('status','positions','statuses','decision')},ensure_ascii=False))
