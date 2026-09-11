"""固定五家公司三起点标准链盘点；不改变数据与预测政策。"""
import argparse
from collections import Counter
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess

from api.alphalake import evaluate
from data_sources.alphalake import AlphaLakeRequest, MissingInputs, content_hash
from tools.compare_valuations import replay
from tools.review_valuation_forecast import review


def audit(plan, policy, export):
    rows = []
    for code in plan['codes']:
        for period in plan['origins']:
            cutoff = period[:4]+'-08-31T16:00:00+00:00'
            data = export(code, period, cutoff)
            row = dict(code=code, period=period, snapshot=data, status='blocked')
            rows.append(row)
            try:
                selected = policy | {'approved_report_period':period}
                if code != '300866':
                    selected['nonfinancial_scope_review'] = '固定五家技术可用性盘点；不沿用安克业务审核，不视为公司范围准入，金融与版本门控仍执行。'
                request = AlphaLakeRequest(data=data, policy=selected)
                run = evaluate(request)
            except (MissingInputs, ValueError) as error:
                row['reason'] = str(error)
                row['missing'] = error.items if isinstance(error,MissingInputs) else []
                continue
            replay(run)
            result = review(run, plan['actual_as_of'], lambda target: export(code,target,plan['actual_as_of']))
            row.update(status='model_and_review_replayed', run=run, review=result)
    return dict(plan_sha256=content_hash(plan), positions=len(rows),
                statuses=dict(Counter(r['status'] for r in rows)), rows=rows,
                boundary=plan['boundary'])


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    parser.add_argument('output',type=Path)
    args = parser.parse_args()
    plan=json.loads((args.directory/'plan.json').read_bytes())
    for path,digest in plan['inputs'].items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest
    policy=json.loads(Path('valuation/research/standard-history-2025H1/policy.json').read_bytes())
    args.output.mkdir(parents=True,exist_ok=False)
    os.environ['ALPHALAKE_VALUATION_RUN_DIR']=str(args.output/'runs')
    def export(code,period,cutoff):
        response=subprocess.run(['./alphalake','export-valuation',plan['database'],code,
            '--period',period,'--as-of',cutoff],check=True,capture_output=True)
        return json.loads(response.stdout)
    result=audit(plan,policy,export)
    raw=json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False).encode()+b'\n'
    (args.output/'result.json.gz').write_bytes(gzip.compress(raw,mtime=0))
    print(json.dumps({k:v for k,v in result.items() if k!='rows'},ensure_ascii=False,indent=2))
