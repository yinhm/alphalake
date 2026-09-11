"""有业务证据的单项训练敏感性；固定评价集合，不产生新采用结论。"""
import gzip
import hashlib
import json
from pathlib import Path

from tools import backtest_tdx_origins as original
from tools.audit_tdx_normalized_scope import audit as audit_business

ROOT=Path(__file__).resolve().parents[3]
DIRECTORY=ROOT/'valuation/research/tdx-growth-expanded'
PROTOCOL_SHA='a8455416217707d605052ca36abb9be8d971832d263d7e6be73c7c9dceccd346'


def load_inputs():
    raw=(DIRECTORY/'q3-training-scope-protocol.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=PROTOCOL_SHA:
        raise ValueError('training scope protocol differs')
    p=json.loads(raw);inputs={}
    for name,ref in p['inputs'].items():
        raw=(ROOT/ref['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=ref['sha256']:
            raise ValueError(name+' hash differs')
        inputs[name]=json.loads(gzip.decompress(raw) if ref['path'].endswith('.gz') else raw)
    return p,inputs


def weights(fit):
    observations=[]
    for row in fit['results']:
        item=dict(code=row['code'],status=row['status'])
        if row['status']=='evaluated':
            predicted=row['forecasts']['current_rule']['ebit']
            item.update(weight=predicted/row['actual']['revenue'],scale=row['actual']['ebit']/predicted)
        else:
            item['reason']=row.get('reason')
        observations.append(item)
    total=sum(r.get('weight',0) for r in observations)
    for row in observations:
        if row['status']=='evaluated':
            row['weight_share']=row['weight']/total
    return dict(fit={k:v for k,v in fit.items() if k!='results'},observations=observations)


def study(p,inputs):
    policy=inputs['protocol'];source=inputs['snapshot']
    dev=original.study(policy,source,'development')
    baseline=original.study(policy,source,'holdout',dev)
    if baseline!={k:v for k,v in inputs['original'].items() if k!='evidence'}:
        raise ValueError('original Q3 results differ')
    business=audit_business(inputs['business_config'])
    if not set(p['excluded_training_codes']) <= {d['code'] for d in inputs['business_config']['documents']}:
        raise ValueError('excluded company lacks business evidence')
    # 只改变训练公司；H1模型选择、原120家公司评价和季度截止保持不变。
    changed=dict(policy,samples=[s for s in policy['samples']
        if s['split']!='development' or s['code'] not in p['excluded_training_codes']])
    fits={o:original.fit_calibration(changed,source,o) for o in policy['origins']}
    rows=original.evaluate(changed,source,'holdout',policy['origins'],tuple(p['models']),fits)
    summary=original.summarize(rows,tuple(p['models']));differences=[]
    for old,new in zip(baseline['results'],rows,strict=True):
        if any(old[k]!=new[k] for k in ('code','origin','status')):
            raise ValueError('evaluation cohort differs')
        item={k:old[k] for k in ('code','origin','status')}
        if old['status']=='evaluated':
            if (old['actual']!=new['actual'] or any(old['forecasts'][m]!=new['forecasts'][m]
                    for m in ('current_rule','zero_growth'))):
                raise ValueError('actuals or control forecasts differ')
            item.update(actual_ebit=old['actual']['ebit'],actual_revenue=old['actual']['revenue'],
                        original_ebit=old['forecasts']['bias_half']['ebit'],
                        sensitivity_ebit=new['forecasts']['bias_half']['ebit'])
        else:
            if old!=new:
                raise ValueError('original refusal differs')
            item['reason']=old.get('reason')
        differences.append(item)
    return dict(protocol_sha256=PROTOCOL_SHA,amount_unit='million_CNY',status='posthoc_training_sensitivity_not_adopted',
        excluded_training_codes=p['excluded_training_codes'],summary=summary,
        diagnostic_original_gates=original.gates(summary,'bias_half',policy['validation'],rows),
        original_validation=baseline['validation'],
        training={o:dict(original=weights(baseline['calibration_training'][o]),sensitivity=weights(fits[o])) for o in policy['origins']},
        results=differences,business_evidence=dict(config_sha256=p['inputs']['business_config']['sha256'],
            checked_pdf_amounts=business['checked_pdf_amounts'],source='existing issuer report excerpts; numerical TDX inputs unchanged'),
        boundary=p['limits'])


if __name__=='__main__':
    print(json.dumps(study(*load_inputs()),ensure_ascii=False,indent=2,allow_nan=False))
