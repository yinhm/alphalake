"""零增长一至三年比较；开发选择须通过并冻结后才打开新留出。"""
import argparse
import hashlib
import json
from pathlib import Path

from tools.backtest_tdx_multiyear_growth import forecast_rows,MODELS
from tools.validate_tdx_zero_growth import verify_sampling,score

ROOT=Path(__file__).resolve().parents[3]


def digest(raw):return hashlib.sha256(raw).hexdigest()


def evaluate(p,source,phase):
    if p['protocol_id']!='tdx-zero-growth-horizons-v1' or (p['baseline'],p['candidate'])!=('flat_first_five','zero_growth'):raise ValueError('unsupported horizon study')
    codes={r['code'] for r in p['samples'] if r['split']==phase}
    if any(r['code'] not in codes for r in source['records']):raise ValueError('unexpected source company')
    inner=p|dict(protocol_id='tdx-multiyear-growth-v1',baseline=MODELS[0],benchmark=MODELS[1],candidate=MODELS[2])
    rows=forecast_rows(inner,source,phase,horizons=(1,2,3))
    out=score(p|dict(protocol_id='tdx-zero-growth-validation-v1'),rows,phase,horizons=(1,2,3))
    out.update(protocol_id=p['protocol_id'],results=rows,diagnostic_only=MODELS[2],boundary=p['boundary'].replace('same unextracted 120 reserved','120 reserved at protocol freeze'))
    return out


def load(p,raw):
    verify_sampling(p);data={}
    for key in ('parent_protocol','development_snapshot','development_definition'):
        content=(ROOT/p[key]).read_bytes()
        if digest(content)!=p[key+'_sha256']:raise ValueError(key+' hash differs')
        data[key]=json.loads(content)
    parent,source,definition=(data[k] for k in ('parent_protocol','development_snapshot','development_definition'))
    if any(parent[k]!=p[k] for k in ('samples','base_policy','windows','evaluation_as_of')):raise ValueError('parent scope differs')
    if definition!=dict(parent_protocol_sha256=p['parent_protocol_sha256'],phase='development',samples=[s for s in p['samples'] if s['split']=='development']):raise ValueError('development definition differs')
    if source['study_sha256']!=p['development_definition_sha256']:raise ValueError('development source binding differs')
    result=evaluate(p,source,'development')
    result['evidence']=dict(protocol_sha256=digest(raw),snapshot_sha256=p['development_snapshot_sha256'],code_sha256=digest(Path(__file__).read_bytes()),helpers={name:digest((ROOT/'valuation/backend'/name).read_bytes()) for name in ['tools/backtest_tdx_multiyear_growth.py','tools/validate_tdx_zero_growth.py','tools/backtest_tdx_cash_revenue.py','tools/backtest_tdx_history.py', 'tools/tdx_research_source.py','data_sources/alphalake.py','engine/module_4_dcf.py']})
    return source,result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('protocol',type=Path);parser.add_argument('--phase',choices=['development','holdout'],required=True);parser.add_argument('--snapshot',type=Path);parser.add_argument('--selection',type=Path);args=parser.parse_args()
    try:
        raw=args.protocol.read_bytes();p=json.loads(raw);source,dev=load(p,raw);out=dev
        if args.phase=='holdout':
            if not dev['decision']['passed'] or args.selection is None or args.snapshot is None:raise ValueError('passing frozen development selection required')
            # Compare before opening either holdout definition or holdout source.
            if json.loads(args.selection.read_bytes())!={k:v for k,v in dev.items() if k!='results'}:raise ValueError('development selection differs')
            definition_raw=args.protocol.with_name('holdout-study.json').read_bytes();definition=json.loads(definition_raw)
            if definition!=dict(parent_protocol_sha256=digest(raw),phase='holdout',samples=[s for s in p['samples'] if s['split']=='holdout']):raise ValueError('holdout definition differs')
            data=args.snapshot.read_bytes();held=json.loads(data)
            if held['study_sha256']!=digest(definition_raw):raise ValueError('holdout source binding differs')
            if held['artifacts']!=source['artifacts'] or held['source_lists']!=source['source_lists']:raise ValueError('holdout source versions differ')
            out=evaluate(p,held,'holdout');out['evidence']=dev['evidence']|dict(snapshot_sha256=digest(data),selection_sha256=digest(args.selection.read_bytes()))
        print(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps(dict(status='rejected',reason=str(exc)),ensure_ascii=False));raise SystemExit(1)


if __name__=='__main__':main()
