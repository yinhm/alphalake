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


def test_expanded_sample_freeze_and_bounded_margin_trials():
    import hashlib
    from tools.backtest_tdx_origins import study as multi
    directory=ROOT/'valuation/research/tdx-growth-expanded'
    raw=(directory/'sampling-universe.json').read_bytes();universe=json.loads(raw)
    protocol=json.loads((directory/'protocol.json').read_bytes())
    assert hashlib.sha256(raw).hexdigest()==protocol['sampling']['universe_sha256']
    ranked=sorted(universe['companies'],key=lambda r:hashlib.sha256((protocol['sampling']['seed']+r['code']).encode()).hexdigest())[:120]
    assert protocol['samples']==[dict(r,split='development' if i%2==0 else 'holdout') for i,r in enumerate(ranked)]
    assert not {r['code'] for r in ranked}&set(universe['excluded_prior_codes'])
    source_raw=(directory/'snapshot.json').read_bytes();source=json.loads(source_raw)
    assert source['study_sha256']==hashlib.sha256((directory/'protocol.json').read_bytes()).hexdigest()
    for version in ('','-v2','-v3'):
        protocol=json.loads((directory/f'protocol{version}.json').read_bytes())
        expected=json.loads((directory/f'development{version}-summary.json').read_bytes())
        assert hashlib.sha256(source_raw).hexdigest()==expected['evidence']['snapshot_sha256']
        actual=multi(protocol,source,'development')
        assert actual['summary']==expected['summary'] and actual['selection']==expected['selection']
        assert actual['selection']['model'] is None
        assert actual['summary']['total']['statuses']=={'evaluated':86,'blocked':34}
    clipped=False
    for row in actual['results']:
        if row['status']!='evaluated':continue
        base=row['base']['ebit']/row['base']['revenue']
        prior=row['prior_full_year']['ebit']/row['prior_full_year']['revenue']
        for model,cap in [('margin_trend_2pp',.02),('margin_trend_5pp',.05)]:
            forecast=row['forecasts'][model]
            expected_margin=base+max(-cap,min(cap,.5*(base-prior)))
            assert forecast['revenue']==row['forecasts']['current_rule']['revenue']
            assert forecast['ebit']/forecast['revenue']==pytest.approx(expected_margin,abs=1e-14)
            clipped |= abs(.5*(base-prior))>cap
    assert clipped
    for cap in (0,-.01,float('nan'),True):
        bad=copy.deepcopy(protocol);bad['candidate_weights']['margin_trend_2pp']['margin_change_cap']=cap
        with pytest.raises(ValueError):multi(bad,source,'development')
    changed=copy.deepcopy(source);held={s['code'] for s in protocol['samples'] if s['split']=='holdout'}
    for row in changed['records']:
        if row['code'] in held:row['bits']['FN86']=bits(999e12)
    assert multi(protocol,changed,'development')==actual


def test_lagged_calibration_only_uses_past_development_labels():
    from tools.backtest_tdx_origins import study as multi
    original,source=fixture()
    for r in copy.deepcopy(source['records'][:4]):
        r['period']=r['period'].replace('2024','2023');r['artifact']=r['artifact'].replace('2024','2023')
        r['bits']['FN314']=bits(int(struct.unpack('<f',struct.pack('<I',r['bits']['FN314']))[0])-10000)
        source['records'].append(r)
        source['artifacts'].append(dict(file=r['artifact'],report_period=r['period'],fetched_at='2026-09-10T12:00:00Z'))
    for r in source['records']:
        if r['period']=='2025-06-30':r['bits']['FN86']=bits(11e6)
    source['records'] += [dict(r,code='000858') for r in copy.deepcopy(source['records'])]
    p=json.loads((ROOT/'valuation/research/tdx-growth-expanded/protocol-v4.json').read_bytes())
    p['samples']=[dict(code='600519',split='development',stratum='test'),dict(code='000858',split='holdout',stratum='test')]
    p['origins']=['2025-06-30'];p['selection']['periods']=p['origins'];p['base_policy']=original['policy']
    p['calibration']['minimum_training_pairs']=1
    result=multi(p,source,'development');training=result['calibration_training']['2025-06-30']
    assert training['training_pairs']==1 and training['evaluation_as_of']=='2025-09-01T00:00:00+08:00'
    assert {r['code'] for r in training['results']}=={'600519'}
    assert training['margin_shift']==pytest.approx(.1-31/420)
    row=result['results'][0]
    assert row['status']=='evaluated'
    for name,w in [('bias_half',.5),('bias_full',1)]:
        f=row['forecasts'][name];baseline=row['forecasts']['current_rule']
        assert f['revenue']==baseline['revenue']
        assert f['ebit']==pytest.approx(baseline['ebit']-baseline['revenue']*w*training['margin_shift'])
    altered=copy.deepcopy(source)
    for r in altered['records']:
        if r['period']>'2025-06-30' or r['code']=='000858':r['bits']['FN86']=bits(999e9)
    changed=multi(p,altered,'development')
    assert changed['calibration_training']==result['calibration_training']
    assert changed['results'][0]['forecasts']==row['forecasts']
    assert changed['results'][0]['actual']!=row['actual']
    late=copy.deepcopy(source)
    for r in late['records']:
        if r['period']=='2023-12-31':r['bits']['FN314']=bits(240902)
    unavailable=multi(p,late,'development')
    assert unavailable['calibration_training']['2025-06-30']['training_pairs']==0
    assert unavailable['results'][0]['reason']=='insufficient past calibration pairs'
    p['calibration']['minimum_training_pairs']=2
    blocked=multi(p,source,'development')
    assert blocked['summary']['total']['statuses']=={'blocked':1}
    assert blocked['calibration_training']['2025-06-30']['margin_shift'] is None
    assert blocked['results'][0]['reason']=='insufficient past calibration pairs'
    p['calibration']['training_split']='holdout'
    with pytest.raises(ValueError,match='calibration policy'):multi(p,source,'development')


def test_archived_lagged_calibration_and_weighted_loss():
    import hashlib
    import math
    from tools.backtest_tdx_origins import study as multi
    directory=ROOT/'valuation/research/tdx-growth-expanded'
    raw=(directory/'snapshot-v4.json').read_bytes();source=json.loads(raw)
    original=json.loads((directory/'snapshot.json').read_bytes())
    assert [r for r in source['records'] if r['period']>='2022-01-01']==original['records']
    assert [a for a in source['artifacts'] if a['report_period']>='2022-01-01']==original['artifacts']
    assert source['study_sha256']==hashlib.sha256((directory/'protocol-v4.json').read_bytes()).hexdigest()
    for version in (4,5,6):
        p=json.loads((directory/f'protocol-v{version}.json').read_bytes())
        expected=json.loads((directory/f'development-v{version}-summary.json').read_bytes())
        assert hashlib.sha256(raw).hexdigest()==expected['evidence']['snapshot_sha256']
        result=multi(p,source,'development')
        assert result['summary']==expected['summary'] and result['selection']==expected['selection']
        assert {k:{a:b for a,b in v.items() if a!='results'} for k,v in result['calibration_training'].items()}==expected['calibration_training']
        assert [t['training_pairs'] for t in result['calibration_training'].values()]==[36,44]
        for origin,t in result['calibration_training'].items():
            assert t['target']==origin and t['origin']<origin
            for r in t['results']:
                assert r['split']=='development'
                if r['status']=='evaluated':
                    assert all(ref['period']<=origin for ref in r['actual']['source_inputs'])
            if version==4:continue
            observations=[]
            for r in t['results']:
                if r['status']!='evaluated':continue
                f=r['forecasts']['current_rule'];a=r['actual']
                observations.append(((f['ebit']-a['ebit'])/f['revenue'],f['revenue']/a['revenue']) if version==5 else (a['ebit']/f['ebit'],f['ebit']/a['revenue']))
            estimate=t['weighted_ebit_margin_error' if version==5 else 'weighted_ebit_scale']
            # 枚举所有残差断点，独立检验加权绝对损失的最小值。
            loss=lambda c:math.fsum(w*abs(x-c) for x,w in observations)
            assert loss(estimate)==pytest.approx(min(loss(x) for x,_ in observations),abs=1e-12)
    assert result['selection']['model']=='bias_half'
    assert result['selection']['gates']['bias_half']['passed']
    assert result['calibration_training']['2023-06-30']['ebit_scale']==.5
    assert result['calibration_training']['2023-06-30']['weighted_ebit_scale']<.5
    for r in result['results']:
        if r['status']=='evaluated':
            scale=result['calibration_training'][r['origin']]['ebit_scale']
            assert r['forecasts']['bias_half']['ebit']==pytest.approx(r['forecasts']['current_rule']['ebit']*(1+scale)/2)
