"""起点分组不读未来、未知信号不归零，原现金结论和完整分母保留。"""
from collections import defaultdict
import copy
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

from tools.audit_tdx_cash_scope import audit,origin_scope
from tools.backtest_tdx_operating_cash import evaluate

ROOT=Path(__file__).resolve().parents[3]
DIR=ROOT/'valuation/research/tdx-operating-cash-forecast'


def test_real_cash_scope_audit_and_origin_only_classification():
    p=json.loads((DIR/'scope-protocol.json').read_text())
    cash=json.loads((ROOT/p['cash_protocol']).read_text());forecast=json.loads((ROOT/p['forecast_protocol']).read_text());source=json.loads((ROOT/p['snapshot']).read_text())
    result=audit(p,cash,forecast,source);recorded=json.loads((DIR/'scope-result.json').read_text())
    assert result=={k:v for k,v in recorded.items() if k!='evidence'}
    for phase in ('development','holdout'):
        r=result['phases'][phase];old=json.loads((DIR/(phase+'-summary.json')).read_text())
        assert r['original_summary']==old['summary'] and r['original_decision']==old['decision']
        assert sum(g['candidates'] for g in r['by_scope'].values())==r['original_summary']['candidates']
        assert sum(g['statuses'].get('evaluated',0) for g in r['by_scope'].values())==r['original_summary']['statuses']['evaluated']
    data=result['phases']['holdout'];group='forecast_ready_no_financial_signal';m=data['by_scope'][group]
    assert m['candidates']==255 and m['statuses']=={'evaluated':244,'blocked':11}
    selected={(r['code'],r['origin']) for r in data['membership'] if r['group']==group}
    rows=[r for r in evaluate(cash,source,'holdout') if (r['code'],r['origin']) in selected and r['status']=='evaluated']
    for model in ('repeat_latest','zero_ocf','mean_two_ocf_margins'):
        errors=[abs(Decimal(r['forecasts'][model]['ocf_cny'])-Decimal(r['actual']['ocf_cny'])) for r in rows]
        expected=sum(float(100*e/Decimal(r['actual']['revenue_cny'])) for e,r in zip(errors,rows))/len(rows)
        assert abs(m['targets']['ocf_cny'][model]['mae_pct_actual_revenue']-expected)<1e-12
    artifacts={a['file']:a for a in source['artifacts']}
    def classify(records):
        index=defaultdict(list)
        for r in records:index[(r['code'],r['period'])].append(r)
        return origin_scope(forecast,index,artifacts,'002410',date(2025,6,30),'2025-09-01T00:00:00+08:00')
    original=classify(source['records']);assert original['group']=='forecast_ready_financial_signal'
    changed=copy.deepcopy(source['records'])
    for r in changed:
        if r['period']>'2025-06-30':r['bits']={}
    assert classify(changed)==original
    changed=copy.deepcopy(source['records'])
    for r in changed:
        if (r['code'],r['period'])==('002410','2025-06-30'):del r['bits']['FN413']
    assert classify(changed)['group']=='financial_signal_unknown'
