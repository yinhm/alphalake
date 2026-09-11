"""新名单排除旧研究，开发回执在打开留出源之前验证。"""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest
from tools.validate_tdx_zero_growth import verify_sampling,load_development,score

ROOT=Path(__file__).resolve().parents[3]
DIR=ROOT/'valuation/research/tdx-zero-growth-validation'


def test_zero_growth_development_and_holdout_gate(tmp_path):
    p=json.loads((DIR/'protocol.json').read_text());verify_sampling(p)
    _,_,rows=load_development(p);actual=score(p,rows,'development');recorded=json.loads((DIR/'development-selection.json').read_text())
    assert actual=={k:v for k,v in recorded.items() if k not in ('evidence','results_reference')}
    assert actual['decision']['passed'] and actual['evaluated_companies']==50
    assert actual['summary']['statuses']=={'blocked':98,'evaluated':202}
    bad=copy.deepcopy(p);bad['samples'][-1]=bad['samples'][0]|dict(split='holdout')
    with pytest.raises(ValueError,match='sampling differs'):verify_sampling(bad)
    fake=copy.deepcopy(recorded);fake['decision']['passed']=False;selection=tmp_path/'selection.json';selection.write_text(json.dumps(fake))
    args=[sys.executable,'-m','tools.validate_tdx_zero_growth',str(DIR/'protocol.json'),'--phase','holdout','--selection',str(selection),'--snapshot',str(tmp_path/'must-not-open.json')]
    result=subprocess.run(args,cwd=ROOT/'valuation/backend',capture_output=True,text=True)
    assert result.returncode==1 and json.loads(result.stdout)['reason']=='development selection differs'


def test_real_zero_growth_holdout_replay_and_future_isolation(tmp_path):
    from decimal import Decimal
    import hashlib
    from tools.backtest_tdx_multiyear_growth import study
    source=json.loads((DIR/'snapshot.json').read_text());p=json.loads((DIR/'protocol.json').read_text())
    recorded=json.loads((DIR/'holdout-result.json').read_text())
    assert hashlib.sha256((DIR/'snapshot.json').read_bytes()).hexdigest()==recorded['evidence']['snapshot_sha256']
    args=[sys.executable,'-m','tools.validate_tdx_zero_growth',str(DIR/'protocol.json'),'--phase','holdout','--selection',str(DIR/'development-selection.json'),'--snapshot',str(DIR/'snapshot.json')]
    # 当前引擎重放不改写当时冻结的选择/结果；临时选择记录当前代码哈希。
    selection=tmp_path/'current-selection.json'
    dev=subprocess.run([sys.executable,'-m','tools.validate_tdx_zero_growth',str(DIR/'protocol.json'),'--phase','development'],cwd=ROOT/'valuation/backend',capture_output=True,text=True)
    assert dev.returncode==0,dev.stdout+dev.stderr
    selection.write_text(dev.stdout);args[args.index('--selection')+1]=str(selection)
    check=subprocess.run(args,cwd=ROOT/'valuation/backend',capture_output=True,text=True)
    assert check.returncode==0,check.stdout+check.stderr
    replay=json.loads(check.stdout)
    assert {k:v for k,v in replay.items() if k!='evidence'}=={k:v for k,v in recorded.items() if k!='evidence'}
    assert replay['evidence']['snapshot_sha256']==recorded['evidence']['snapshot_sha256']
    assert recorded['decision']['passed'] and recorded['evaluated_companies']==103
    assert recorded['summary']['statuses']=={'evaluated':406,'blocked':194}
    valid=[r for r in recorded['results'] if r['status']=='evaluated']
    for r in valid:
        assert r['actual_fcff'] is None
        assert r['forecasts']['zero_growth']['revenue']==r['base']['revenue']
    for model in ('flat_first_five','zero_growth'):
        errors=[abs(Decimal(str(r['forecasts'][model]['revenue']))-Decimal(str(r['actual']['revenue']))) for r in valid]
        mae=sum(100*e/Decimal(str(r['actual']['revenue'])) for e,r in zip(errors,valid))/len(valid)
        wape=100*sum(errors)/sum(Decimal(str(r['actual']['revenue'])) for r in valid)
        assert float(mae)==pytest.approx(recorded['summary']['models'][model]['revenue_mae_pct'],rel=1e-12)
        assert float(wape)==pytest.approx(recorded['summary']['models'][model]['revenue_wape_pct'],rel=1e-12)
    window=recorded['by_window']['2024-06-30:2']['models']
    assert window['zero_growth']['revenue_mae_pct']>window['flat_first_five']['revenue_mae_pct']
    parent=json.loads((ROOT/p['parent_protocol']).read_text());sample=next(r for r in valid if r['horizon']==3)
    one=parent|dict(samples=[s for s in p['samples'] if s['code']==sample['code']],windows=[dict(origin=sample['origin'],horizon=3)])
    changed=copy.deepcopy(source)
    for r in changed['records']:
        if (r['code'],r['period'])==(sample['code'],sample['target']):r['bits']['FN230']+=1000000
    after=study(one,changed,'holdout')['results'][0]
    assert after['forecasts']==sample['forecasts'] and after['rule_evidence']==sample['rule_evidence']
    assert after['actual']['revenue']!=sample['actual']['revenue']
    bad=copy.deepcopy(source);bad['study_sha256']='0'*64;path=tmp_path/'bad.json';path.write_text(json.dumps(bad))
    check=subprocess.run(args[:-1]+[str(path)],cwd=ROOT/'valuation/backend',capture_output=True,text=True)
    assert check.returncode==1 and json.loads(check.stdout)['reason']=='holdout source binding differs'
