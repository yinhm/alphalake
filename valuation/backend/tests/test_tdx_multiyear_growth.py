"""多年路径独立复算与起点/未来隔离；不删除未来亏损。"""
import copy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from tools.backtest_tdx_multiyear_growth import study,MODELS

ROOT=Path(__file__).resolve().parents[3]
DIR=ROOT/'valuation/research/tdx-multiyear-growth'


def test_real_multiyear_paths_and_future_isolation(tmp_path):
    p=json.loads((DIR/'protocol.json').read_text());source=json.loads((ROOT/p['snapshot']).read_text())
    result=study(p,source);recorded=json.loads((DIR/'development-result.json').read_text())
    assert result=={k:v for k,v in recorded.items() if k!='evidence'}
    assert result['summary']['candidates']==300 and result['summary']['statuses']=={'blocked':98,'evaluated':202}
    assert result['decision']['passed'] is False and result['decision']['checks']['zero_growth_revenue_nonworse'] is False
    valid=[r for r in result['results'] if r['status']=='evaluated'];assert any(r['actual']['ebit']<0 for r in valid)
    for r in valid:
        assert r['actual_fcff'] is None
        assert r['forecasts'][MODELS[0]]['annual'][0]==r['forecasts'][MODELS[2]]['annual'][0]
        g=Decimal(str(r['rule_evidence']['clipped_scenario_growth']));t=Decimal(str(p['base_policy']['terminal_growth']))
        for model in MODELS:
            revenue=Decimal(str(r['base']['revenue']))
            for year in range(1,r['horizon']+1):
                growth=Decimal(0) if model==MODELS[1] else g if model==MODELS[0] else g+(t-g)*Decimal(year-1)/4
                revenue*=1+growth
            assert float(revenue)==pytest.approx(r['forecasts'][model]['revenue'],rel=1e-12)
    for model in MODELS:
        errors=[abs(Decimal(str(r['forecasts'][model]['revenue']))-Decimal(str(r['actual']['revenue']))) for r in valid]
        mae=sum(100*e/Decimal(str(r['actual']['revenue'])) for e,r in zip(errors,valid))/len(valid)
        wape=100*sum(errors)/sum(Decimal(str(r['actual']['revenue'])) for r in valid)
        assert float(mae)==pytest.approx(result['summary']['models'][model]['revenue_mae_pct'],rel=1e-12)
        assert float(wape)==pytest.approx(result['summary']['models'][model]['revenue_wape_pct'],rel=1e-12)
    sample=next(r for r in valid if r['horizon']==3)
    one=p|dict(samples=[s for s in p['samples'] if s['code']==sample['code']],windows=[dict(origin=sample['origin'],horizon=3)])
    changed=copy.deepcopy(source)
    for r in changed['records']:
        if (r['code'],r['period'])==(sample['code'],sample['target']):r['bits']['FN230']+=1000000
    after=study(one,changed)['results'][0]
    assert sample['forecasts']==after['forecasts'] and sample['rule_evidence']==after['rule_evidence']
    assert sample['actual']['revenue']!=after['actual']['revenue']
    with pytest.raises(ValueError,match='duplicate forecast window'):study(p|dict(windows=p['windows']*2),source)
    bad=p|dict(snapshot_sha256='0'*64);path=tmp_path/'protocol.json';path.write_text(json.dumps(bad))
    check=subprocess.run([sys.executable,'-m','tools.backtest_tdx_multiyear_growth',str(path)],cwd=ROOT/'valuation/backend',text=True,capture_output=True)
    assert check.returncode==1 and json.loads(check.stdout)['status']=='rejected'
    assert hashlib.sha256((ROOT/p['snapshot']).read_bytes()).hexdigest()==p['snapshot_sha256']
