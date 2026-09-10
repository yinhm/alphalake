"""简化回溯的时点隔离、源位精度与缺项分母。"""
import copy
from datetime import date, timedelta
import json
from pathlib import Path
import struct

import pytest
from tools.backtest_tdx_history import run, available

ROOT=Path(__file__).resolve().parents[3]
STUDY=ROOT/'valuation/research/tdx-history/study.json'


def bits(n):return struct.unpack('<I',struct.pack('<f',n))[0]


def fixture():
    study=json.loads(STUDY.read_text());study['samples']=study['samples'][:1]
    snapshot=dict(contract_version='tdx-history-source-v1',artifacts=[],records=[])
    for year,months in [(2024,[3,6,9,12]),(2025,[3,6,9,12]),(2026,[3,6])]:
        for month in months:
            period=date(year,month+1,1)-timedelta(days=1) if month<12 else date(year,12,31)
            announced=period+timedelta(days=60)
            q=month//3;revenue={2024:100e6,2025:110e6,2026:121e6}[year]
            values={f:0 for f in ('FN305','FN306','FN83','FN82','FN301','FN506','FN509','FN510','FN413')}
            values.update(FN230=revenue,FN86=q*revenue*.1,FN314=int(announced.strftime('%y%m%d')))
            filename=f'gpcw{period:%Y%m%d}.zip'
            snapshot['artifacts'].append(dict(file=filename,report_period=period.isoformat(),fetched_at='2026-09-10T12:00:00Z'))
            snapshot['records'].append(dict(code=study['samples'][0]['code'],period=period.isoformat(),artifact=filename,bits={f:bits(n) for f,n in values.items()}))
    return study,snapshot


def test_replay_uses_origin_only_and_preserves_rejections():
    study,snapshot=fixture()
    result=run(study,snapshot);row=result['results'][0]
    assert row['status']=='evaluated'
    assert row['forecast']['revenue']==pytest.approx(462)
    assert row['errors']['current_rule']['revenue_error_pct']==pytest.approx(0)
    assert row['errors']['zero_growth']['revenue_error_pct']<0
    changed=copy.deepcopy(snapshot);changed['records'][-1]['bits']['FN230']=bits(150e6)
    later=run(study,changed)['results'][0]
    assert row['forecast']==later['forecast'] and row['rule_evidence']==later['rule_evidence']
    assert row['actual']!=later['actual']
    changed=copy.deepcopy(snapshot);changed['records'][5]['bits']['FN314']=bits(250902)
    blocked=run(study,changed)
    assert blocked['summary']['candidates']==1 and blocked['summary']['statuses']=={'blocked':1}
    assert blocked['summary']['models']['current_rule']['revenue_median_ape_pct'] is None
    changed=copy.deepcopy(snapshot);changed['records'][0]['bits']['FN314']=bits(240231)
    assert run(study,changed)['results'][0]['diagnostics']
    changed=copy.deepcopy(snapshot);changed['records'].append(copy.deepcopy(changed['records'][0]))
    assert 'duplicate' in run(study,changed)['results'][0]['reason']
    changed=copy.deepcopy(snapshot);del changed['records'][2]['bits']['FN230']
    assert run(study,changed)['results'][0]['status']=='blocked'
    changed=copy.deepcopy(snapshot);changed['records'][5]['bits']['FN506']=bits(1)
    flagged=run(study,changed)
    assert flagged['summary']['models']['current_rule']['revenue_n']==1
    assert flagged['summary']['models']['current_rule']['ebit_n']==1
    assert flagged['by_profit_scope']['financial_fields_present']['models']['current_rule']['ebit_n']==1
    assert flagged['by_profit_scope']['no_financial_fields_detected']['models']['current_rule']['ebit_n']==0
    changed=copy.deepcopy(snapshot);changed['records'][0]['bits']['FN230']=bits(float('nan'))
    assert run(study,changed)['results'][0]['status']=='blocked'
    changed=copy.deepcopy(study);changed['forecast_as_of']='2025-09-01'
    with pytest.raises(ValueError,match='timezone'):run(changed,snapshot)
    # 日期精度一律中国次日零点；不能把公告日盘前当作已知。
    r=copy.deepcopy(snapshot['records'][5]);r['bits']['FN314']=bits(250831)
    assert available(r,snapshot['artifacts'][5]).isoformat()=='2025-09-01T00:00:00+08:00'


def test_archived_tdx_baseline():
    import hashlib
    directory=STUDY.parent
    raw=(directory/'snapshot.json').read_bytes()
    snapshot=json.loads(raw);study=json.loads(STUDY.read_bytes())
    expected=json.loads((directory/'baseline.json').read_bytes())
    assert hashlib.sha256(raw).hexdigest()==expected['evidence']['snapshot_sha256']
    assert hashlib.sha256(STUDY.read_bytes()).hexdigest()==snapshot['study_sha256']==expected['evidence']['study_sha256']
    actual=run(study,snapshot)
    # 扩展评价分母，不改源数据、逐公司预测、实际或误差，也不覆盖v1历史基线。
    for before,after in zip(expected['results'],actual['results'],strict=True):
        assert before=={k:v for k,v in after.items() if k not in ('profit_scope','profit_basis')}
    summary=json.loads((directory/'mixed-business-summary.json').read_bytes())
    assert {k:actual[k] for k in summary}==summary
    assert actual['summary']['statuses']=={'evaluated':19,'blocked':1}
    assert actual['summary']['models']['current_rule']['ebit_n']==19
    assert actual['by_profit_scope']['no_financial_fields_detected']['models']['current_rule']['ebit_n']==13
    assert actual['by_profit_scope']['financial_fields_present']['models']['current_rule']['ebit_n']==6
    assert {r['code'] for r in actual['results'] if r.get('profit_scope')=='financial_fields_present'}=={'600519','600887','000895','000333','000651','600031'}
    assert actual['by_profit_scope']['not_evaluated']['candidates']==1
    assert actual['by_split']['holdout']['models']['current_rule']['revenue_median_ape_pct']>actual['by_split']['holdout']['models']['zero_growth']['revenue_median_ape_pct']


def test_multi_origin_selection_is_separate_from_holdout():
    from tools.backtest_tdx_origins import study as multi, gates
    # 已有2025合成数据只测研究流程，不作为新增真实留出证据。
    original,source=fixture();source['records'] += [dict(r,code='000858') for r in copy.deepcopy(source['records'])]
    protocol=json.loads((ROOT/'valuation/research/tdx-multi-origin/protocol.json').read_text())
    protocol['samples']=[dict(code='600519',split='development',stratum='test'),dict(code='000858',split='holdout',stratum='test')]
    protocol['origins']=['2025-06-30'];protocol['selection']['periods']=['2025-06-30']
    protocol['base_policy']=original['policy']
    dev=multi(protocol,source,'development')
    assert {r['code'] for r in dev['results']}=={'600519'}
    assert dev['selection']['model'] is None
    altered=copy.deepcopy(source)
    for r in altered['records']:
        if r['code']=='000858':r['bits']['FN230']=bits(999e6)
    assert multi(protocol,altered,'development')==dev
    held=multi(protocol,source,'holdout',dev)
    assert {r['code'] for r in held['results']}=={'000858'}
    assert set(held['summary']['total']['models'])=={'current_rule','zero_growth'}
    changed=copy.deepcopy(source)
    for r in changed['records']:
        if r['period']=='2026-06-30':r['bits']['FN86']=bits(999e6)
    later=multi(protocol,changed,'development')
    assert later['results'][0]['forecasts']==dev['results'][0]['forecasts']
    assert later['results'][0]['actual']!=dev['results'][0]['actual']
    assert dev['results'][0]['prior_full_year']['revenue']==pytest.approx(400)
    summary=copy.deepcopy(dev['summary'])
    for group in [summary['total'],*summary['by_origin'].values()]:
        for model in group['models'].values():
            model.update(ebit_n=24,ebit_mae_pct_actual_revenue=10,revenue_wape_pct=10)
        group['models']['half_growth']['ebit_mae_pct_actual_revenue']=9
    assert gates(summary,'half_growth',protocol['selection'])['passed']
    summary['by_origin']['2025-06-30']['models']['half_growth']['ebit_mae_pct_actual_revenue']=11
    assert not gates(summary,'half_growth',protocol['selection'])['passed']


def test_archived_multi_origin_failure_and_selection_tamper(tmp_path):
    import hashlib
    import subprocess
    import sys
    from tools.backtest_tdx_origins import study as multi
    directory=ROOT/'valuation/research/tdx-multi-origin'
    protocol=json.loads((directory/'protocol-v2.json').read_bytes())
    raw=(directory/'snapshot.json').read_bytes();source=json.loads(raw)
    dev=json.loads((directory/'development-v2-summary.json').read_bytes())
    held=json.loads((directory/'holdout-v2-summary.json').read_bytes())
    assert hashlib.sha256(raw).hexdigest()==held['evidence']['snapshot_sha256']==protocol['source_snapshot_sha256']
    actual_dev=multi(protocol,source,'development')
    assert actual_dev['selection']==dev['selection']
    assert actual_dev['summary']==dev['summary']
    actual=multi(protocol,source,'holdout',actual_dev)
    assert {k:actual[k] for k in held if k!='evidence'}=={k:v for k,v in held.items() if k!='evidence'}
    assert actual['summary']['total']['statuses']=={'evaluated':14,'blocked':2}
    assert not actual['validation']['verdict']['passed']
    assert not actual['validation']['verdict']['checks']['primary_improvement']
    # 当前代码重新生成选择证据，再篡改所选模型，不能凭修改后的JSON打开其他候选。
    args=[sys.executable,'-m','tools.backtest_tdx_origins',str(directory/'protocol-v2.json'),str(directory/'snapshot.json')]
    replay=subprocess.run(args+['--phase','development'],capture_output=True,text=True,check=True)
    changed=json.loads(replay.stdout)
    changed['selection']['model']='quarter_growth_half_margin'
    selection=tmp_path/'selection.json';selection.write_text(json.dumps(changed))
    rejected=subprocess.run(args+['--phase','holdout','--selection',str(selection)],capture_output=True,text=True)
    assert rejected.returncode==1
    assert json.loads(rejected.stdout)['reason']=='saved selection does not reproduce development decision'
