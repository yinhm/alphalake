"""冻结现金收入比较：独立公式、未来隔离、共同分母及来源绑定。"""
import copy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from tools.backtest_tdx_cash_revenue import study,MODELS

ROOT=Path(__file__).resolve().parents[3]
DIRECTORY=ROOT/'valuation/research/tdx-cash-revenue-comparison'


def test_real_cash_revenue_comparison_and_future_isolation(tmp_path):
    p=json.loads((DIRECTORY/'protocol.json').read_text());source=json.loads((ROOT/p['snapshot']).read_text())
    result=study(p,source)
    recorded=json.loads((DIRECTORY/'development-result.json').read_text())
    assert {k:v for k,v in recorded.items() if k!='evidence'}==result
    assert result['candidates']==180 and result['baseline_evaluable']==164
    valid=[r for r in result['results'] if r['status']=='evaluated'];assert len(valid)==128
    assert result['statuses']=={'blocked':16,'evaluated':128,'blocked_candidate':36}
    assert result['decision']['passed'] is False
    assert result['decision']['checks']['primary_improvement'] is False
    assert result['decision']['checks']['wape_nonworse'] is False
    for r in valid:
        a,b=r['forecasts'].values();now=r['current'];prior=r['prior']
        ocf=(Decimal(now['ocf_cny'])+Decimal(prior['ocf_cny'])*Decimal(now['revenue_cny'])/Decimal(prior['revenue_cny']))/2
        assert Decimal(a['ocf'])==ocf
        assert Decimal(b['ocf'])==ocf*Decimal(b['revenue'])/Decimal(now['revenue_cny'])
        assert a['capex']==b['capex'] and r['actual_fcff'] is None
    for model in MODELS:
        errors=[abs(Decimal(r['forecasts'][model]['ocf'])-Decimal(r['actual']['ocf'])) for r in valid]
        expected=sum(float(e/Decimal(r['actual']['revenue_cny'])*100) for e,r in zip(errors,valid))/len(valid)
        assert abs(result['summary']['ocf'][model]['mae_pct_actual_revenue']-expected)<1e-12
    sample=next(r for r in valid if r['origin']=='2025-06-30')
    one=p|dict(samples=[s for s in p['samples'] if s['code']==sample['code']],origins=['2025-06-30'])
    before=study(one,source)['results'][0]
    changed=copy.deepcopy(source)
    for r in changed['records']:
        if r['code']==sample['code'] and r['period']=='2026-06-30':r['bits']['FN234']+=100000
    after=study(one,changed)['results'][0]
    assert before['forecasts']==after['forecasts'] and before['revenue_evidence']==after['revenue_evidence']
    assert before['actual']['ocf']!=after['actual']['ocf']
    changed=copy.deepcopy(source)
    for r in changed['records']:
        if r['code']==sample['code'] and r['period']=='2025-06-30':
            import struct
            r['bits']['FN314']=struct.unpack('<I',struct.pack('<f',250902))[0]
    refused=study(one,changed)['results'][0]
    assert refused['status']=='blocked' and refused['baseline_status']=='blocked'
    bad=copy.deepcopy(p);bad['snapshot_sha256']='0'*64;path=tmp_path/'protocol.json';path.write_text(json.dumps(bad))
    out=subprocess.run([sys.executable,'-m','tools.backtest_tdx_cash_revenue',str(path)],cwd=ROOT/'valuation/backend',capture_output=True,text=True)
    assert out.returncode==1 and json.loads(out.stdout)['status']=='rejected'
    assert hashlib.sha256((ROOT/p['snapshot']).read_bytes()).hexdigest()==p['snapshot_sha256']
