"""简化回溯的时点隔离、源位精度与缺项分母。"""
import copy
from datetime import date, timedelta
from decimal import Decimal
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


def test_fixed_replication_preserves_training_and_holdout_denominators():
    import hashlib
    from tools.backtest_tdx_origins import study as multi
    directory=ROOT/'valuation/research/tdx-growth-expanded'
    old_protocol=json.loads((directory/'protocol-v6.json').read_bytes())
    old_source=json.loads((directory/'snapshot-v4.json').read_bytes())
    p=json.loads((directory/'protocol-v7.json').read_bytes());source=json.loads((directory/'snapshot-v7.json').read_bytes())
    universe=json.loads((directory/'sampling-universe.json').read_bytes())
    ranked=sorted(universe['companies'],key=lambda r:hashlib.sha256((p['sampling']['seed']+r['code']).encode()).hexdigest())
    assert [s for s in p['samples'] if s['split']=='holdout']==[dict(r,split='holdout') for r in ranked[120:240]]
    train_codes={s['code'] for s in p['samples'] if s['split']=='development'}
    assert [r for r in source['records'] if r['code'] in train_codes]==[r for r in old_source['records'] if r['code'] in train_codes]
    assert not {s['code'] for s in p['samples'] if s['split']=='holdout'}&{s['code'] for s in old_protocol['samples']}
    old_dev=multi(old_protocol,old_source,'development');new_dev=multi(p,source,'development')
    assert old_dev['selection']==new_dev['selection'] and old_dev['calibration_training']==new_dev['calibration_training']
    for version,protocol,snapshot,dev in [(6,old_protocol,old_source,old_dev),(7,p,source,new_dev)]:
        actual=multi(protocol,snapshot,'holdout',dev)
        expected=json.loads((directory/f'holdout-v{version}-summary.json').read_bytes())
        assert actual['summary']==expected['summary'] and actual['validation']==expected['validation']
        assert actual['calibration_training']==dev['calibration_training']
        assert actual['validation']['verdict']['passed']==(version==7)
    assert actual['summary']['total']['statuses']=={'blocked':69,'evaluated':171}
    p['fixed_validation_model']='bias_full'
    with pytest.raises(ValueError,match='frozen replication model'):multi(p,source,'holdout',new_dev)


def test_training_company_influence_is_a_fixed_model_diagnostic():
    from tools.backtest_tdx_origins import fit_calibration,evaluate,summarize
    directory=ROOT/'valuation/research/tdx-growth-expanded'
    p=json.loads((directory/'protocol-v7.json').read_bytes());source=json.loads((directory/'snapshot-v7.json').read_bytes())
    expected=json.loads((directory/'training-influence-v7.json').read_bytes())['results']
    assert {r['excluded_training_code'] for r in expected}=={s['code'] for s in p['samples'] if s['split']=='development'}
    for saved in expected:
        changed=p|dict(samples=[s for s in p['samples'] if s['code']!=saved['excluded_training_code']])
        fits={o:fit_calibration(changed,source,o) for o in p['origins']}
        assert {o:v['ebit_scale'] for o,v in fits.items()}==saved['scales']
        rows=evaluate(changed,source,'holdout',p['origins'],('current_rule','zero_growth','bias_half'),fits)
        summary=summarize(rows,('current_rule','zero_growth','bias_half'));m=summary['total']['models']
        assert m['bias_half']['ebit_n']==saved['evaluated_pairs']==171
        assert m['bias_half']['ebit_mae_pct_actual_revenue']==saved['ebit_error']
        assert 1-saved['ebit_error']/m['current_rule']['ebit_mae_pct_actual_revenue']==saved['improvement_fraction']
        assert {o:v['models']['bias_half']['ebit_mae_pct_actual_revenue'] for o,v in summary['by_origin'].items()}==saved['by_origin']
    assert min(r['improvement_fraction'] for r in expected)>.07


def test_calibration_publication_rejects_tampered_evidence_and_weights():
    from tools.prepare_forecast_calibration import prepare
    from data_sources.alphalake_calibration import weighted_median
    directory=ROOT/'valuation/research/tdx-growth-expanded'
    protocol=(directory/'protocol-v7.json').read_bytes();source=(directory/'snapshot-v7.json').read_bytes();validation=(directory/'holdout-v7-summary.json').read_bytes()
    args=('2026-06-30',['300866'],'2026-09-10T23:00:00Z')
    with pytest.raises(ValueError,match='hash differs'):prepare(protocol,source+b' ',validation,*args)
    changed=json.loads(validation);changed['validation']['verdict']['passed']=False
    with pytest.raises(ValueError,match='validation does not reproduce'):prepare(protocol,source,json.dumps(changed).encode(),*args)
    for pairs in ([],[(1,float('inf'))],[(1,0)],[(1,1e308)]*30):
        with pytest.raises(ValueError):weighted_median(pairs)


def test_additional_temporal_window_keeps_failed_effect_gate():
    from tools.backtest_tdx_origins import study as multi
    directory=ROOT/'valuation/research/tdx-growth-expanded'
    p=json.loads((directory/'protocol-v8.json').read_bytes());source=json.loads((directory/'snapshot-v7.json').read_bytes())
    dev=multi(p,source,'development');old=json.loads((directory/'development-v7-summary.json').read_bytes())
    assert dev['selection']==old['selection'] and dev['summary']==old['summary']
    result=multi(p,source,'holdout',dev);expected=json.loads((directory/'holdout-v8-summary.json').read_bytes())
    assert result['summary']==expected['summary'] and result['validation']==expected['validation']
    assert result['summary']['total']['statuses']=={'blocked':35,'evaluated':85}
    assert [k for k,v in result['validation']['verdict']['checks'].items() if not v]==['primary_improvement']
    training=result['calibration_training']['2025-06-30']
    assert training['origin']=='2024-06-30' and training['target']=='2025-06-30'
    assert training['evaluation_as_of']=='2025-09-01T00:00:00+08:00'
    assert training['training_pairs']==42
    assert '追加2025时间窗口' in result['boundary']


def test_cash_source_additions_preserve_existing_evidence():
    import hashlib
    directory=ROOT/'valuation/research/tdx-growth-expanded'
    raw=(directory/'snapshot-cash-proxy.json').read_bytes();source=json.loads(raw)
    old=json.loads((directory/'snapshot-v7.json').read_bytes());summary=json.loads((directory/'cash-source-summary.json').read_bytes())
    assert hashlib.sha256(raw).hexdigest()==summary['snapshot_sha256']
    assert hashlib.sha256((directory/'protocol-cash-proxy.json').read_bytes()).hexdigest()==summary['protocol_sha256']==source['study_sha256']
    assert source['artifacts']==old['artifacts'] and source['source_lists']==old['source_lists']
    assert len(source['records'])==summary['records']==3861
    for before,after in zip(old['records'],source['records'],strict=True):
        assert {'FN234','FN114'}<=after['bits'].keys()
        assert before==after|dict(bits={k:v for k,v in after['bits'].items() if k not in ('FN234','FN114')})


def test_cash_proxy_periods_missing_values_and_real_diagnostic():
    import hashlib
    from tools.backtest_tdx_cash_proxy import cash_actual,diagnose
    original,source=fixture()
    for r in source['records']:
        year=int(r['period'][:4]);q=int(r['period'][5:7])//3
        r['bits']['FN234']=bits([-10,20,30,40][q-1]*1e6)
        r['bits']['FN114']=bits(q*{2024:10,2025:12,2026:15}[year]*1e6)
    actual=cash_actual(source,'600519',date(2026,6,30),original['evaluation_as_of'])
    assert actual['operating_cash_flow']==80 and actual['capital_expenditure_cash']==54
    assert actual['reported_ocf_less_capex']==26
    assert [r['coefficient'] for r in actual['source_inputs']]==[1,1,1,1,1,1,-1]
    missing=copy.deepcopy(source)
    del next(r for r in missing['records'] if r['period']=='2025-09-30')['bits']['FN234']
    with pytest.raises(KeyError):cash_actual(missing,'600519',date(2026,6,30),original['evaluation_as_of'])
    late=copy.deepcopy(source)
    next(r for r in late['records'] if r['period']=='2026-06-30')['bits']['FN314']=bits(260909)
    with pytest.raises(KeyError):cash_actual(late,'600519',date(2026,6,30),'2026-09-01T00:00:00+08:00')
    bad=copy.deepcopy(source)
    next(r for r in bad['records'] if r['period']=='2025-06-30')['bits']['FN114']=bits(100e6)
    with pytest.raises(ValueError,match='negative cumulative'):cash_actual(bad,'600519',date(2026,6,30),original['evaluation_as_of'])
    directory=ROOT/'valuation/research/tdx-growth-expanded'
    p=json.loads((directory/'protocol-cash-proxy.json').read_bytes());raw=(directory/'snapshot-cash-proxy.json').read_bytes();source=json.loads(raw)
    expected=json.loads((directory/'cash-diagnostic-summary.json').read_bytes());assert hashlib.sha256(raw).hexdigest()==expected['evidence']['snapshot_sha256']
    result=diagnose(p,source)
    assert {k:v for k,v in result.items() if k!='results'}=={k:v for k,v in expected.items() if k!='evidence'}
    assert result['summary']['cash_statuses']=={'blocked_forecast':104,'evaluated':255,'blocked_cash_inputs':1}
    assert all(r['actual_fcff'] is None for r in result['results'])
    rejected=[r for r in result['results'] if r['cash_status']=='blocked_cash_inputs']
    assert [(r['code'],r['origin']) for r in rejected]==[('603315','2025-06-30')]
    changed=copy.deepcopy(source)
    sample=next(r for r in result['results'] if r['cash_status']=='evaluated')
    target=str(int(sample['origin'][:4])+1)+'-06-30'
    row=next(r for r in changed['records'] if r['code']==sample['code'] and r['period']==target)
    row['bits']['FN234']=bits(999e9)
    later=diagnose(p,changed)
    for a,b in zip(result['results'],later['results'],strict=True):assert a.get('forecast_fcff_policy')==b.get('forecast_fcff_policy')
    changed_row=next(r for r in later['results'] if (r['code'],r['origin'])==(sample['code'],sample['origin']))
    assert changed_row['cash_actual']!=sample['cash_actual']


def test_cash_anomaly_original_documents_and_tampering():
    import hashlib
    from tools.backtest_tdx_cash_proxy import verify_anomaly
    directory=ROOT/'valuation/research/tdx-growth-expanded'
    evidence=directory/'cash-review-603315'
    ledger=json.loads((evidence/'evidence.json').read_bytes())
    raw=(directory/'snapshot-cash-proxy.json').read_bytes();source=json.loads(raw)
    assert hashlib.sha256(raw).hexdigest()==ledger['source_snapshot_sha256']
    result=verify_anomaly(ledger,evidence,source)
    assert result==dict(status='source_values_confirmed_comparability_unresolved',
                       tdx_capex_ttm_cny='-26679990.5',pdf_capex_ttm_cny='-26679991.35',actual_fcff=None)
    for key,value_,message in [('sha256','0'*64,'hash'),('values',['0.01','6353617.85'],'row/columns'),
                               ('header_period','2024年1—6月','scope'),('row_page',42,'row/columns')]:
        bad=copy.deepcopy(ledger);bad['reports'][0][key]=value_
        with pytest.raises(ValueError,match=message):verify_anomaly(bad,evidence,source)
    bad=copy.deepcopy(source)
    next(r for r in bad['records'] if (r['code'],r['period'])==('603315','2025-06-30'))['bits']['FN114']^=1
    with pytest.raises(ValueError,match='TDX bits'):verify_anomaly(ledger,evidence,bad)


def test_reinvestment_coverage_preserves_sources_and_rejects_ambiguous_inputs():
    import hashlib
    from collections import defaultdict
    from tools.audit_tdx_reinvestment import audit,component
    directory=ROOT/'valuation/research/tdx-growth-expanded'
    raw=(directory/'snapshot-reinvestment.json').read_bytes();source=json.loads(raw)
    previous=json.loads((directory/'snapshot-cash-proxy.json').read_bytes())
    p=json.loads((directory/'protocol-reinvestment.json').read_bytes())
    expected=json.loads((directory/'reinvestment-coverage-summary.json').read_bytes())
    assert source['study_sha256']==hashlib.sha256((directory/'protocol-reinvestment.json').read_bytes()).hexdigest()
    assert hashlib.sha256(raw).hexdigest()==expected['evidence']['snapshot_sha256']
    assert source['artifacts']==previous['artifacts'] and source['source_lists']==previous['source_lists']
    for old,new in zip(previous['records'],source['records'],strict=True):
        assert old==new|dict(bits={f:new['bits'][f] for f in old['bits']})
    result=audit(p,source)
    assert {k:v for k,v in result.items() if k!='results'}=={k:v for k,v in expected.items() if k!='evidence'}
    assert result['summary']['candidates']==480 and all(r['actual_fcff'] is None for r in result['results'])
    assert result['summary']['source_groups_available']['depreciation_extensions']==0
    invalid=copy.deepcopy(p);invalid['source_rules']['multipliers']['FN581']=1
    with pytest.raises(ValueError,match='source rules'):audit(invalid,source)
    original,synthetic=fixture();index=defaultdict(list)
    for r in synthetic['records']:
        r['bits'].update(FN579=bits(1.25),FN581=bits(2),FN114=bits(100),FN234=bits(100),FN11=bits(int(r['period'][:4])))
        index[(r['code'],r['period'])].append(r)
    artifacts={a['file']:a for a in synthetic['artifacts']}
    def part(field,cutoff=original['evaluation_as_of']):
        return component(index,artifacts,'600519',date(2026,6,30),field,cutoff)
    assert Decimal(part('FN579')['value_cny'])==12500
    assert Decimal(part('FN581')['value_cny'])==20000
    assert Decimal(part('FN114')['value_cny'])==100
    assert Decimal(part('FN234')['value_cny'])==400
    assert Decimal(part('FN11')['value_cny'])==1
    row=index[('600519','2026-06-30')][0]
    row['bits']['FN579']=bits(0)
    assert part('FN579')['issues']==['source_zero_ambiguous'] and part('FN579')['value_cny'] is None
    del row['bits']['FN579']
    assert part('FN579')['issues']==['missing_field']
    row['bits']['FN579']=0x7f800000
    assert part('FN579')['issues']==['invalid_source']
    index[('600519','2025-06-30')][0]['bits']['FN114']=bits(1000)
    assert part('FN114')['issues']==['negative_cumulative_capex']
    assert part('FN581','2026-07-01T00:00:00+08:00')['issues']==['unavailable_at_cutoff']
    index[('600519','2026-06-30')].append(copy.deepcopy(row))
    assert part('FN581')['issues']==['duplicate_identity']


def test_depreciation_scope_reconciliation_and_tampering():
    import hashlib
    from tools.verify_tdx_depreciation import verify
    directory=ROOT/'valuation/research/tdx-growth-expanded/depreciation-review-688648'
    ledger=json.loads((directory/'evidence.json').read_bytes())
    raw=(directory.parent/'snapshot-reinvestment.json').read_bytes();source=json.loads(raw)
    assert hashlib.sha256(raw).hexdigest()==ledger['snapshot_sha256']
    result=verify(ledger,directory,source)
    expected=json.loads((directory/'reconciled.json').read_bytes())
    assert result=={k:v for k,v in expected.items() if k!='evidence'}
    assert Decimal(result['reported_da_ttm_cny'])==Decimal('60616347.9423828125')
    assert Decimal(result['pdf_reported_da_ttm_cny'])==Decimal('60616286.29')
    assert result['actual_fcff'] is None
    yearly=next(r for r in result['periods'] if r['period']=='2025-12-31')
    assert [r['field'] for r in yearly['source_inputs']]==['FN136','FN137','FN138']
    assert yearly['unconsumed_source_fields']=={'FN579':'included_in_FN136','FN581':'included_in_FN136'}
    for key,change,message in [('sha256','0'*64,'hash'),('fn136_components',['ppe','investment_property','right_of_use'],'scope')]:
        bad=copy.deepcopy(ledger);bad['reports'][0][key]=change
        with pytest.raises(ValueError,match=message):verify(bad,directory,source)
    bad=copy.deepcopy(ledger);bad['reports'][0]['rows'][0]['values'][0]='0.01'
    with pytest.raises(ValueError,match='amounts'):verify(bad,directory,source)
    bad=copy.deepcopy(ledger);bad['reports'][0]['rows'][0]['header_page']=1
    with pytest.raises(ValueError,match='section/unit'):verify(bad,directory,source)
    bad=copy.deepcopy(source)
    next(r for r in bad['records'] if (r['code'],r['period'])==('688648','2025-06-30'))['bits']['FN581']^=1
    with pytest.raises(ValueError,match='TDX bits'):verify(ledger,directory,bad)
    bad=copy.deepcopy(source)
    next(r for r in bad['records'] if (r['code'],r['period'])==('688648','2025-12-31'))['bits']['FN581']=bits(529.9)
    with pytest.raises(ValueError,match='combined-row'):verify(ledger,directory,bad)


def test_cash_bridge_keeps_residual_and_quarter_rounding_limits():
    from tools.verify_tdx_depreciation import verify
    directory=ROOT/'valuation/research/tdx-growth-expanded/depreciation-review-688648'
    ledger=json.loads((directory/'cash-evidence.json').read_bytes())
    source=json.loads((directory.parent/'snapshot-reinvestment.json').read_bytes())
    result=verify(ledger,directory,source)
    expected=json.loads((directory/'cash-reconciled.json').read_bytes())
    assert result=={k:v for k,v in expected.items() if k!='evidence'}
    assert result['actual_fcff'] is None
    ttm=result['cash_bridge_ttm']['source']
    assert Decimal(ttm['operating_cashflow'])==Decimal('-312692516')
    assert Decimal(ttm['working_capital_cash_adjustment'])==Decimal('-287512928')
    assert Decimal(ttm['unclassified_other_adjustments'])==Decimal('5582351.8076171875')
    assert [len(r['cash_bridge']['ocf_quarters']) for r in result['periods']]==[2,4,2]
    bad=copy.deepcopy(ledger);bad['reports'][0]['cash_rows'].pop()
    with pytest.raises(ValueError,match='rows incomplete'):verify(bad,directory,source)
    bad=copy.deepcopy(ledger);bad['reports'][0]['cash_rows'][1]['values'][0]='0.00'
    with pytest.raises(ValueError,match='amounts'):verify(bad,directory,source)
    bad=copy.deepcopy(source)
    next(r for r in bad['records'] if (r['code'],r['period'])==('688648','2025-06-30'))['bits']['FN146']^=1
    with pytest.raises(ValueError,match='cash TDX bits'):verify(ledger,directory,bad)
    bad=copy.deepcopy(source)
    next(r for r in bad['records'] if (r['code'],r['period'])==('688648','2025-03-31'))['bits']['FN234']=bits(0)
    with pytest.raises(ValueError,match='rounding bound'):verify(ledger,directory,bad)
    bad=copy.deepcopy(ledger);bad['evaluation_as_of']='2026-07-01T00:00:00+08:00'
    with pytest.raises(ValueError,match='cutoff'):verify(bad,directory,source)


def test_disclosed_cash_adjustments_keep_blank_and_unexplained_items():
    from tools.verify_tdx_depreciation import verify
    directory=ROOT/'valuation/research/tdx-growth-expanded/depreciation-review-688648'
    ledger=json.loads((directory/'adjustment-evidence.json').read_bytes())
    source=json.loads((directory.parent/'snapshot-reinvestment.json').read_bytes())
    result=verify(ledger,directory,source)
    expected=json.loads((directory/'adjustment-reconciled.json').read_bytes())
    assert result=={k:v for k,v in expected.items() if k!='evidence'}
    assert result['actual_fcff'] is None
    terms=result['adjustment_ttm_terms']
    assert sum(Decimal(r['observed_terms_sum_cny']) for r in terms.values())==Decimal('5582391.58')
    assert terms['adj_other']==dict(complete=False,observed_terms_sum_cny='-1030263.34',reported_blank_periods=['2026-06-30','2025-06-30'])
    assert terms['adj_disposal']['complete'] is False
    assert terms['adj_disposal']['reported_blank_periods']==['2026-06-30']
    assert sum(len(r['adjustment_breakdown']['checked_source_inputs']) for r in result['periods'])==8
    bad=copy.deepcopy(ledger);bad['reports'][0]['adjustment_rows'].pop()
    with pytest.raises(ValueError,match='incomplete'):verify(bad,directory,source)
    bad=copy.deepcopy(ledger);bad['reports'][1]['adjustment_rows'][-1]['values'][0]='0.00'
    with pytest.raises(ValueError,match='amounts'):verify(bad,directory,source)
    bad=copy.deepcopy(ledger)
    next(r for r in bad['reports'][2]['adjustment_blanks'] if r['key']=='adj_disposal')['current_columns']=[62,85]
    with pytest.raises(ValueError,match='blank current'):verify(bad,directory,source)
    bad=copy.deepcopy(source)
    next(r for r in bad['records'] if (r['code'],r['period'])==('688648','2025-06-30'))['bits']['FN301']^=1
    with pytest.raises(ValueError,match='adjustment TDX bits'):verify(ledger,directory,bad)


def test_capex_frozen_development_and_information_isolation():
    import hashlib
    from collections import defaultdict
    from tools.backtest_tdx_capex import study,financial
    directory=ROOT/'valuation/research/tdx-capex-forecast'
    raw=(directory/'snapshot.json').read_bytes();source=json.loads(raw);p=json.loads((directory/'protocol.json').read_bytes())
    expected=json.loads((directory/'development-summary.json').read_bytes())
    assert hashlib.sha256(raw).hexdigest()==expected['evidence']['snapshot_sha256']
    assert source['study_sha256']==hashlib.sha256((directory/'protocol.json').read_bytes()).hexdigest()
    old=ROOT/'valuation/research/tdx-growth-expanded'
    universe=json.loads((old/'sampling-universe.json').read_bytes())
    ranked=sorted(universe['companies'],key=lambda r:hashlib.sha256(('alphalake-growth-v3:'+r['code']).encode()).hexdigest())
    assert [r['code'] for r in p['samples'] if r['split']=='holdout']==[r['code'] for r in ranked[240:360]]
    previous=json.loads((old/'protocol-v7.json').read_bytes())
    assert [r for r in p['samples'] if r['split']=='development']==[r for r in previous['samples'] if r['split']=='development']
    result=study(p,source,'development')
    assert {k:v for k,v in result.items() if k!='results'}=={k:v for k,v in expected.items() if k!='evidence'}
    assert result['summary']['statuses']=={'blocked':22,'evaluated':158}
    assert result['decision']['selected'] is None
    with pytest.raises(ValueError,match='passing development'):study(p,source,'holdout',result)
    changed=copy.deepcopy(source);held={r['code'] for r in p['samples'] if r['split']=='holdout'}
    for row in changed['records']:
        if row['code'] in held:row['bits']['FN114']=bits(999e9)
    assert study(p,changed,'development')==result
    selected=next(r for r in result['results'] if r['origin']=='2025-06-30' and r['status']=='evaluated')
    changed=copy.deepcopy(source)
    next(r for r in changed['records'] if (r['code'],r['period'])==(selected['code'],'2026-06-30'))['bits']['FN114']=bits(999e9)
    later=study(p,changed,'development')
    assert [r.get('forecasts_cny') for r in later['results']]==[r.get('forecasts_cny') for r in result['results']]
    assert later['summary']!=result['summary']
    original,synthetic=fixture();index=defaultdict(list)
    for row in synthetic['records']:
        row['bits']['FN114']=bits(0);index[(row['code'],row['period'])].append(row)
    artifacts={a['file']:a for a in synthetic['artifacts']}
    def current():return financial(index,artifacts,'600519',date(2026,6,30),original['evaluation_as_of'])
    assert Decimal(current()['capex_cny'])==0
    index[('600519','2025-06-30')][0]['bits']['FN114']=bits(100)
    with pytest.raises(ValueError,match='negative capex'):current()
    index[('600519','2025-06-30')][0]['bits']['FN114']=bits(0)
    index[('600519','2026-06-30')].append(copy.deepcopy(index[('600519','2026-06-30')][0]))
    with pytest.raises(ValueError,match='duplicate'):current()


def test_capex_lagged_calibration_keeps_past_forecast_cutoff():
    from tools.backtest_tdx_capex import study,fit_scale
    directory=ROOT/'valuation/research/tdx-capex-forecast';p=json.loads((directory/'protocol-v2.json').read_bytes());source=json.loads((directory/'snapshot.json').read_bytes())
    result=study(p,source,'development');expected=json.loads((directory/'development-v2-summary.json').read_bytes())
    assert {k:v for k,v in result.items() if k!='results'}=={k:v for k,v in expected.items() if k!='evidence'}
    assert result['decision']['selected'] is None
    v=result['decision']['verdicts']['lagged_scale_half']
    assert {k for k,passed in v['checks'].items() if not passed}=={'primary_improvement','capex_wape_nonworse'}
    training=fit_scale(p,source,'2023-06-30')
    assert training['forecast_as_of']=='2022-09-01T00:00:00+08:00' and training['evaluation_as_of']=='2023-09-01T00:00:00+08:00'
    assert training['training_pairs']==52
    code=next(r['code'] for r in training['results'] if r['status']=='evaluated')
    changed=copy.deepcopy(source)
    next(r for r in changed['records'] if (r['code'],r['period'])==(code,'2022-06-30'))['bits']['FN314']=bits(221001)
    late=fit_scale(p,changed,'2023-06-30')
    assert late['training_pairs']==51
    assert next(r for r in late['results'] if r['code']==code)['reason']=='source not available at cutoff: 2022-06-30'
    changed=copy.deepcopy(source)
    next(r for r in changed['records'] if (r['code'],r['period'])==(code,'2024-06-30'))['bits']['FN114']=bits(999e9)
    assert fit_scale(p,changed,'2023-06-30')==training


def test_capex_trend_failure_and_forged_selection_cannot_open_holdout(tmp_path,monkeypatch,capsys):
    import sys
    import tools.backtest_tdx_capex as module
    directory=ROOT/'valuation/research/tdx-capex-forecast'
    p=json.loads((directory/'protocol-v3.json').read_bytes());source=json.loads((directory/'snapshot.json').read_bytes())
    result=module.study(p,source,'development');expected=json.loads((directory/'development-v3-summary.json').read_bytes())
    assert {k:v for k,v in result.items() if k!='results'}=={k:v for k,v in expected.items() if k!='evidence'}
    assert result['decision']['selected'] is None
    candidate='recent_trend_half'
    assert not result['decision']['verdicts'][candidate]['checks']['primary_improvement']
    perfect=copy.deepcopy(result['results'])
    for r in perfect:
        if r['status']=='evaluated':r['errors'][candidate]=dict(error_cny='0',error_pct_actual_revenue=0)
    assert module.gates(p,perfect,candidate)['passed']
    bad=copy.deepcopy(expected);bad['decision']['selected']=candidate;bad['decision']['verdicts'][candidate]['passed']=True
    receipt=tmp_path/'forged-selection.json';receipt.write_text(json.dumps(bad))
    evaluate=module.evaluate
    def guarded(protocol,snapshot,split,models,calibrations=None):
        assert split=='development','holdout must not be evaluated after forged selection'
        return evaluate(protocol,snapshot,split,models,calibrations)
    monkeypatch.setattr(module,'evaluate',guarded)
    monkeypatch.setattr(sys,'argv',['backtest_tdx_capex',str(directory/'protocol-v3.json'),str(directory/'snapshot.json'),'--phase','holdout','--selection',str(receipt)])
    with pytest.raises(SystemExit) as error:module.main()
    assert error.value.code==1 and 'does not reproduce' in capsys.readouterr().out


def test_capex_cip_original_evidence_and_source_bits():
    import hashlib,re
    from pypdf import PdfReader
    from tools.backtest_tdx_history import value
    directory=ROOT/'valuation/research/tdx-capex-forecast'
    ledger=json.loads((directory/'driver-evidence.json').read_text());raw=(directory/'snapshot-v4.json').read_bytes();source=json.loads(raw)
    assert hashlib.sha256(raw).hexdigest()==ledger['snapshot_sha256']
    p=json.loads((directory/'protocol-v4.json').read_bytes())
    assert source['study_sha256']==hashlib.sha256((directory/'protocol-v4.json').read_bytes()).hexdigest()
    assert next(s for s in p['samples'] if s['code']=='688648')['split']=='evidence_review'
    old=json.loads((directory/'snapshot.json').read_bytes());index={(r['code'],r['period']):r for r in source['records']}
    for row in old['records']:
        current=index[(row['code'],row['period'])]
        assert current['artifact']==row['artifact']
        assert {f:current['bits'][f] for f in row['bits']}==row['bits']
    for report in ledger['reports']:
        path=directory/ledger['pdf_directory']/report['file']
        assert hashlib.sha256(path.read_bytes()).hexdigest()==report['sha256']
        pdf=PdfReader(path)
        def text(page):return re.sub(r'\s+','',pdf.pages[page-1].extract_text()).replace(':','：')
        assert ledger['code'] in text(1) and report['title'] in text(1)
        header=text(report['header_page'])
        assert '合并资产负债表' in header and '单位：元币种：人民币' in header
        y,m,d=map(int,report['period'].split('-'));assert f'{y}年{m}月{d}日' in header
        def check(page,values,record):
            matches=re.findall(r'在建工程七、22((?:[0-9,]+\.[0-9]{2})+)',text(page))
            assert len(matches)==1
            amounts=[s.replace(',','') for s in re.findall(r'[0-9,]+\.[0-9]{2}',matches[0])]
            assert amounts==values
            assert record['bits']['FN28']==bits(float(Decimal(amounts[0])))
        record=index[(ledger['code'],report['period'])]
        check(report['page'],report['values'],record)
        changed=copy.deepcopy(record);changed['bits']['FN28']+=1
        with pytest.raises(AssertionError):check(report['page'],report['values'],changed)
        with pytest.raises(AssertionError):check(report['page'],['0',report['values'][1]],record)
        capex=report['capex_row'];body=text(capex['page'])
        matches=re.findall(re.escape(capex['label'])+r'((?:[0-9,]+\.[0-9]{2})+)',body)
        assert len(matches)==1
        assert [s.replace(',','') for s in re.findall(r'[0-9,]+\.[0-9]{2}',matches[0])]==capex['values']
        assert record['bits']['FN114']==bits(float(capex['values'][capex['column']]))
        if report['file']=='2025H1.pdf':
            assert ledger['payment_note']['text'] in text(ledger['payment_note']['page'])
            with pytest.raises(AssertionError):check(65,report['values'],record)


def test_capex_cip_group_boundary_training_and_no_fallback():
    from collections import defaultdict
    from tools.backtest_tdx_capex import cip_group,fit_cip_scale,evaluate,BASE
    directory=ROOT/'valuation/research/tdx-capex-forecast'
    p=json.loads((directory/'protocol-v4.json').read_bytes());source=json.loads((directory/'snapshot-v4.json').read_bytes())
    index=defaultdict(list)
    for r in source['records']:index[(r['code'],r['period'])].append(r)
    artifacts={a['file']:a for a in source['artifacts']};end=date(2025,6,30);row=index[('688648',end.isoformat())][0]
    def group(capex):return cip_group(index,artifacts,'688648',end,'2025-09-01T00:00:00+08:00',Decimal(capex))
    row['bits']['FN28']=bits(50)
    assert group('100')['group']=='high' and group('101')['group']=='low'
    for n in (0,-1):
        row['bits']['FN28']=bits(n)
        with pytest.raises(ValueError,match='nonpositive'):group('100')
    row['bits'].pop('FN28')
    with pytest.raises((KeyError,ValueError)):group('100')
    source=json.loads((directory/'snapshot-v4.json').read_bytes())
    fitted=fit_cip_scale(p,source,'2025-06-30')
    assert fitted['forecast_as_of']=='2024-09-01T00:00:00+08:00'
    assert all(r['driver']['period']=='2024-06-30' for r in fitted['results'] if r['status']=='evaluated')
    changed=copy.deepcopy(source)
    for r in changed['records']:
        if r['period']>'2025-06-30' or r['code'] in {s['code'] for s in p['samples'] if s['split']=='holdout'}:r['bits']['FN28']=bits(999)
    assert fit_cip_scale(p,changed,'2025-06-30')==fitted
    small=dict(p,samples=[s for s in p['samples'] if s['split']=='development'][:9])
    fit=fit_cip_scale(small,source,'2025-06-30')
    assert all(g['multiplier'] is None for g in fit['groups'].values())
    rows=evaluate(dict(small,origins=['2025-06-30']),source,'development',(BASE,'cip_group_scale_half'),{'2025-06-30':fit})
    assert all(r['status']=='blocked' and 'forecasts_cny' not in r for r in rows)
    from tools.backtest_tdx_capex import study
    actual=study(p,source,'development');saved=json.loads((directory/'development-v4-summary.json').read_bytes())
    assert {k:v for k,v in actual.items() if k!='results'}=={k:v for k,v in saved.items() if k!='evidence'}
    assert actual['summary']['statuses']=={'blocked':38,'evaluated':142}
    assert actual['decision']['selected'] is None
    with pytest.raises(ValueError,match='passing development'):study(p,source,'holdout',actual)


def test_working_cash_signed_components_cutoff_and_prediction_isolation():
    from collections import defaultdict
    from tools.backtest_tdx_working_cash import observation,evaluate
    p=json.loads((ROOT/'valuation/research/tdx-working-cash-forecast/protocol.json').read_bytes());_,source=fixture()
    p=dict(p,origins=['2025-06-30'],samples=[dict(code=source['records'][0]['code'],split='development')])
    for row in source['records']:
        q=int(row['period'][5:7])//3
        row['bits'].update(FN146=bits(q*10),FN147=bits(-q*20),FN148=bits(q*30))
    index=defaultdict(list)
    for r in source['records']:index[(r['code'],r['period'])].append(r)
    artifacts={a['file']:a for a in source['artifacts']};code=p['samples'][0]['code']
    def current():return observation(index,artifacts,code,date(2025,6,30),'2025-09-01T00:00:00+08:00')
    assert Decimal(current()['working_cash_cny'])==80
    before=evaluate(p,source,'development')[0]
    assert before['status']=='evaluated' and before['forecasts_cny']==dict(repeat_latest='80',zero_forecast='0',half_latest='40')
    changed=copy.deepcopy(source)
    for row in changed['records']:
        if row['period']>'2025-06-30':row['bits']['FN148']=bits(10000)
    assert evaluate(p,changed,'development')[0]['forecasts_cny']==before['forecasts_cny']
    row=index[(code,'2025-06-30')][0];original=copy.deepcopy(row)
    row['bits']['FN146']=bits(0)
    with pytest.raises(ValueError,match='source_zero_ambiguous'):current()
    row['bits']=copy.deepcopy(original['bits']);row['bits']['FN314']=bits(250901)
    with pytest.raises(ValueError,match='unavailable_at_cutoff'):current()
    row['bits']=copy.deepcopy(original['bits']);index[(code,'2025-06-30')].append(row)
    with pytest.raises(ValueError,match='duplicate_identity'):current()
    index[(code,'2025-06-30')].pop()
    # Nonzero signed source components may legitimately cancel; the aggregate is not missing.
    for r in source['records']:r['bits']['FN148']=bits(int(r['period'][5:7])//3*10)
    assert Decimal(current()['working_cash_cny'])==0


def test_working_cash_real_source_and_development_receipt(tmp_path,monkeypatch,capsys):
    import hashlib,sys
    import tools.backtest_tdx_working_cash as module
    directory=ROOT/'valuation/research/tdx-working-cash-forecast'
    p=json.loads((directory/'protocol.json').read_bytes());source=json.loads((directory/'snapshot.json').read_bytes())
    assert source['study_sha256']==hashlib.sha256((directory/'protocol.json').read_bytes()).hexdigest()
    capex=json.loads((ROOT/'valuation/research/tdx-capex-forecast/protocol.json').read_bytes())
    assert p['samples']==capex['samples']
    old=json.loads((ROOT/'valuation/research/tdx-growth-expanded/snapshot-reinvestment.json').read_bytes())
    index={(r['code'],r['period']):r for r in old['records']}
    dev={s['code'] for s in p['samples'] if s['split']=='development'}
    for r in source['records']:
        if r['code'] in dev:
            prior=index[(r['code'],r['period'])]
            assert r['artifact']==prior['artifact']
            assert all(prior['bits'][f]==v for f,v in r['bits'].items())
    actual=module.study(p,source,'development')
    saved=json.loads((directory/'development-summary.json').read_bytes())
    assert {k:v for k,v in actual.items() if k!='results'}=={k:v for k,v in saved.items() if k!='evidence'}
    assert actual['summary']['statuses']=={'blocked':13,'evaluated':167}
    assert {k for k,v in actual['decision']['checks'].items() if not v}=={'zero_benchmark_nonworse'}
    # Independently sum Decimal errors from forecasts/actuals, not the stored error fields.
    valid=[r for r in actual['results'] if r['status']=='evaluated']
    for model in module.MODELS:
        errors=[abs(Decimal(r['forecasts_cny'][model])-Decimal(r['actual']['working_cash_cny'])) for r in valid]
        primary=sum(e/Decimal(r['actual']['revenue_cny'])*100 for e,r in zip(errors,valid))/len(valid)
        wape=sum(errors)*100/sum(abs(Decimal(r['actual']['working_cash_cny'])) for r in valid)
        assert actual['summary']['models'][model]['mae_pct_actual_revenue']==pytest.approx(float(primary),rel=1e-14)
        assert actual['summary']['models'][model]['wape_pct']==float(wape)
    changed=copy.deepcopy(source)
    for r in changed['records']:
        if r['code'] not in dev:r['bits']['FN148']=bits(999999)
    assert module.study(p,changed,'development')==actual
    # A fabricated passed decision cannot bypass CLI reproduction before holdout evaluation.
    fake={k:v for k,v in actual.items() if k!='results'};fake['decision']=dict(passed=True,checks={});fake['evidence']={}
    receipt=tmp_path/'forged.json';receipt.write_text(json.dumps(fake))
    evaluate=module.evaluate
    def guarded(p,s,split):
        assert split=='development'
        return evaluate(p,s,split)
    monkeypatch.setattr(module,'evaluate',guarded)
    monkeypatch.setattr(sys,'argv',['working_cash',str(directory/'protocol.json'),str(directory/'snapshot.json'),'--phase','holdout','--selection',str(receipt)])
    with pytest.raises(SystemExit) as exc:module.main()
    assert exc.value.code==1 and 'development selection failed' in capsys.readouterr().out


def test_working_cash_component_diagnostic_cancellation_and_development_only(monkeypatch,capsys):
    import sys
    import tools.backtest_tdx_working_cash as module
    def report(values):return dict(components={f:dict(value_cny=str(v)) for f,v in zip(('FN146','FN147','FN148'),values)},working_cash_cny=str(sum(values)),revenue_cny='100')
    row=dict(status='evaluated',base=report([10,-20,30]),actual=report([-10,20,30]),forecasts_cny=dict(repeat_latest='20',zero_forecast='0',half_latest='10'))
    result=module.component_diagnostics([row,dict(status='blocked')])
    assert result['statuses']==dict(evaluated=1,blocked=1)
    assert result['components']['FN146']['direction']==dict(same=0,opposite=1,either_zero=0)
    # Signed repeat errors +20,-40,0 have gross 60 and net 20; half errors +15,-30,-15 have gross60/net30.
    assert result['cancellation']['repeat_latest']['sum_component_absolute_error_pct_revenue']==60
    assert result['cancellation']['repeat_latest']['net_absolute_error_pct_revenue']==20
    assert result['cancellation']['half_latest']['net_absolute_error_pct_revenue']==30
    assert result['cancellation']['repeat_latest']['offset_fraction']==pytest.approx(2/3)
    assert module.component_diagnostics([])['cancellation']['repeat_latest']['offset_fraction'] is None
    directory=ROOT/'valuation/research/tdx-working-cash-forecast';p=json.loads((directory/'protocol.json').read_bytes());source=json.loads((directory/'snapshot.json').read_bytes())
    study=module.study(p,source,'development');saved=json.loads((directory/'component-diagnostics.json').read_bytes())
    assert module.component_diagnostics(study['results'])==saved['component_diagnostics']['overall']
    assert {o:module.component_diagnostics([r for r in study['results'] if r['origin']==o]) for o in p['origins']}==saved['component_diagnostics']['by_origin']
    for field in ('FN146','FN147','FN148'):
        for s in saved['component_diagnostics']['by_origin'].values():
            m=s['components'][field]['metrics']['models']
            assert m['zero_forecast']['mae_pct_actual_revenue']<m['half_latest']['mae_pct_actual_revenue']<m['repeat_latest']['mae_pct_actual_revenue']
    monkeypatch.setattr(sys,'argv',['working_cash','absent-protocol','absent-source','--phase','holdout','--diagnose-components'])
    with pytest.raises(SystemExit) as exc:module.main()
    assert exc.value.code==1 and 'development only' in capsys.readouterr().out


def test_inventory_gross_net_cash_bridge_and_tampering():
    import hashlib,re
    from pypdf import PdfReader
    from tools.verify_tdx_depreciation import verify,inventory_bridge
    directory=ROOT/'valuation/research/tdx-growth-expanded/depreciation-review-688648';ledger=json.loads((directory/'inventory-evidence.json').read_bytes())
    raw=(directory.parent/'snapshot-reinvestment.json').read_bytes();source=json.loads(raw)
    assert hashlib.sha256(raw).hexdigest()==ledger['snapshot_sha256']
    result=verify(ledger,directory,source);expected=json.loads((directory/'inventory-reconciled.json').read_bytes())
    assert result=={k:v for k,v in expected.items() if k!='evidence'}
    bridges=[p['inventory_bridge'] for p in result['periods']]
    assert [b['provision_decrease_cny'] for b in bridges]==['5798345.50','918810.87','5601493.61']
    for b in bridges:
        assert Decimal(b['net_decrease_cny'])+Decimal(b['provision_decrease_cny'])==Decimal(b['pdf_cash_adjustment_cny'])
        assert Decimal(b['source_net_minus_pdf_cny'])!=0
    assert result['actual_fcff'] is None
    report=ledger['reports'][0];pdf=PdfReader(directory/report['file']);pages={}
    def text(page):
        if page not in pages:pages[page]=re.sub(r'\s+','',pdf.pages[page-1].extract_text())
        return pages[page]
    record=next(r for r in source['records'] if r['code']=='688648' and r['period']==report['period']);cash=Decimal(bridges[0]['pdf_cash_adjustment_cny'])
    bad=copy.deepcopy(report);bad['inventory_table']['values'][0]='407062150.40'
    with pytest.raises(ValueError,match='table values'):inventory_bridge(bad,text,record,cash)
    bad=copy.deepcopy(report);bad['inventory_balance']['page']=65;bad['inventory_balance']['header_page']=65
    with pytest.raises(ValueError,match='balance scope'):inventory_bridge(bad,text,record,cash)
    bad=copy.deepcopy(record);bad['bits']['FN17']=bits(float(bridges[0]['gross_close_cny']))
    with pytest.raises(ValueError,match='source bits'):inventory_bridge(report,text,bad,cash)
    with pytest.raises(ValueError,match='cash adjustment'):inventory_bridge(report,text,record,Decimal(bridges[0]['net_decrease_cny']))
    bad=copy.deepcopy(ledger);bad['reports'][0].pop('inventory_table')
    with pytest.raises(ValueError,match='partial inventory'):verify(bad,directory,source)


def test_working_cash_inventory_driver_signal_and_source_integrity():
    from collections import defaultdict
    from tools.backtest_tdx_working_cash import inventory_signal,evaluate,study
    directory=ROOT/'valuation/research/tdx-working-cash-forecast';p=json.loads((directory/'protocol-v2.json').read_bytes())
    _,source=fixture();code=source['records'][0]['code'];index=defaultdict(list)
    for r in source['records']:
        r['bits'].update(FN17=bits(150 if r['period']>='2025-01-01' else 100),FN230=bits(25),FN146=bits(10),FN147=bits(20),FN148=bits(-5))
        index[(code,r['period'])].append(r)
    artifacts={a['file']:a for a in source['artifacts']}
    # Add previous same-season revenue quarters absent from the minimal fixture.
    for period,announced in [('2023-09-30',231120),('2023-12-31',240220)]:
        name='gpcw'+period.replace('-','')+'.zip';artifacts[name]=dict(file=name,report_period=period,fetched_at='2026-09-10T12:00:00Z')
        r=dict(code=code,period=period,artifact=name,bits=dict(FN314=bits(announced),FN230=bits(25)));index[(code,period)].append(r);source['records'].append(r)
    source['artifacts']=list(artifacts.values())
    def signal():return inventory_signal(index,artifacts,code,date(2025,6,30),'2025-09-01T00:00:00+08:00')
    assert signal()[0]==25  # Half of 150 - 100 * (100/100), not a change in actual FN146.
    one=dict(p,origins=['2025-06-30'],samples=[dict(code=code,split='development')]);before=evaluate(one,source,'development')[0]
    assert before['forecasts_cny']['inventory_intensity_half']=='25'
    changed=copy.deepcopy(source)
    for r in changed['records']:
        if r['period']>'2025-06-30':r['bits']['FN17']=bits(9999);r['bits']['FN146']=bits(9999)
    after=evaluate(one,changed,'development')[0]
    assert after['forecasts_cny']==before['forecasts_cny'] and after['actual']!=before['actual']
    row=index[(code,'2024-06-30')][0];row['bits']['FN17']=bits(0)
    with pytest.raises(ValueError,match='zero ambiguous'):signal()
    row['bits']['FN17']=bits(100);row['bits']['FN314']=bits(250901)
    with pytest.raises(ValueError,match='cutoff'):signal()
    source=json.loads((directory/'snapshot-v2.json').read_bytes());old=json.loads((directory/'snapshot.json').read_bytes());index={(r['code'],r['period']):r for r in source['records']}
    assert len(source['records'])==len(old['records'])
    for r in old['records']:
        current=index[(r['code'],r['period'])]
        assert current['artifact']==r['artifact'] and all(current['bits'][f]==v for f,v in r['bits'].items())
    audited=json.loads((ROOT/'valuation/research/tdx-growth-expanded/snapshot-reinvestment.json').read_bytes())
    audited_index={(r['code'],r['period']):r for r in audited['records']}
    dev={s['code'] for s in p['samples'] if s['split']=='development'}
    for r in source['records']:
        if r['code'] in dev:assert r['bits']['FN17']==audited_index[(r['code'],r['period'])]['bits']['FN17']
    actual=study(p,source,'development')
    assert actual['summary']['statuses']=={'blocked':17,'evaluated':163}
    assert actual['decision']['passed'] is False
    assert {k for k,v in actual['decision']['checks'].items() if not v}=={'zero_benchmark_nonworse'}
    saved=json.loads((directory/'development-v2-summary.json').read_bytes())
    assert {k:v for k,v in actual.items() if k!='results'}=={k:v for k,v in saved.items() if k!='evidence'}


def test_working_cash_business_diagnosis_keeps_all_groups_and_refusals(monkeypatch,capsys):
    import sys
    import tools.backtest_tdx_working_cash as module
    directory=ROOT/'valuation/research/tdx-working-cash-forecast';p=json.loads((directory/'protocol-v2.json').read_bytes());source=json.loads((directory/'snapshot-v2.json').read_bytes())
    r=module.study(p,source,'development');diagnosis=module.business_diagnostics(p,r['results']);saved=json.loads((directory/'business-diagnostics.json').read_bytes())
    assert diagnosis==saved['business_diagnostics']
    assert diagnosis['industries']==26 and diagnosis['development_companies']==60
    groups=diagnosis['by_industry'].values()
    assert sum(g['companies'] for g in groups)==60 and max(g['companies'] for g in groups)==11
    assert sum(g['summary']['candidates'] for g in groups)==180
    assert sum(diagnosis['refusals'].values())==17
    held=next(s['code'] for s in p['samples'] if s['split']=='holdout')
    with pytest.raises(ValueError,match='development only'):module.business_diagnostics(p,[dict(code=held,status='blocked')])
    monkeypatch.setattr(sys,'argv',['working_cash','absent','absent','--phase','holdout','--diagnose-business'])
    with pytest.raises(SystemExit) as exc:module.main()
    assert exc.value.code==1 and 'development only' in capsys.readouterr().out


def test_working_cash_expansion_sampling_source_and_failed_replication():
    import hashlib
    from tools.backtest_tdx_working_cash import study,metrics,business_diagnostics
    directory=ROOT/'valuation/research/tdx-working-cash-forecast';p=json.loads((directory/'protocol-v3.json').read_bytes());old=json.loads((directory/'protocol-v2.json').read_bytes())
    universe=json.loads((ROOT/'valuation/research/tdx-growth-expanded/sampling-universe.json').read_bytes())
    ranked=sorted(universe['companies'],key=lambda r:hashlib.sha256(('alphalake-growth-v3:'+r['code']).encode()).hexdigest())
    original=[s for s in old['samples'] if s['split']=='development'];dev=[s for s in p['samples'] if s['split']=='development'];held=[s for s in p['samples'] if s['split']=='holdout']
    assert dev==original+[dict(s,split='development') for s in ranked[360:960]]
    assert held==[s for s in old['samples'] if s['split']=='holdout']
    assert len({s['code'] for s in p['samples']})==780
    assert p['definitions']==old['definitions'] and p['gates']==old['gates'] and p['driver_policy']==old['driver_policy']
    raw=(directory/'snapshot-v3.json').read_bytes();source=json.loads(raw);prior=json.loads((directory/'snapshot-v2.json').read_bytes());index={(r['code'],r['period']):r for r in source['records']}
    assert source['study_sha256']==hashlib.sha256((directory/'protocol-v3.json').read_bytes()).hexdigest()
    assert all(index[(r['code'],r['period'])]==r for r in prior['records'])
    result=study(p,source,'development');saved=json.loads((directory/'development-v3-summary.json').read_bytes())
    assert all(result[k]==saved[k] for k in ('summary','by_origin','decision'))
    assert result['summary']['statuses']=={'blocked':202,'evaluated':1778}
    assert {k for k,v in result['decision']['checks'].items() if v}=={'minimum_pairs'}
    original_codes={s['code'] for s in original};models=(p['baseline'],p['benchmark'],p['candidate'])
    assert metrics([r for r in result['results'] if r['code'] in original_codes],models)==saved['development_cohorts']['original60']==json.loads((directory/'development-v2-summary.json').read_bytes())['summary']
    assert metrics([r for r in result['results'] if r['code'] not in original_codes],models)==saved['development_cohorts']['additional600']
    assert business_diagnostics(p,result['results'])==saved['business_diagnostics']


def test_business_break_original_values_timing_and_tampering(tmp_path):
    import hashlib
    from datetime import datetime,timezone
    from tools.verify_tdx_business_break import verify
    directory=ROOT/'valuation/research/tdx-working-cash-forecast/review-000809';ledger=json.loads((directory/'evidence.json').read_bytes());raw=(directory.parent/'snapshot-v3.json').read_bytes();source=json.loads(raw)
    assert hashlib.sha256(raw).hexdigest()==ledger['snapshot_sha256']
    result=verify(ledger,directory,source);saved=json.loads((directory/'reconciled.json').read_bytes())
    assert result=={k:v for k,v in saved.items() if k!='evidence'}
    assert [r['original_available_from'] for r in result['periods']]==['2022-08-21T00:00:00+08:00','2023-08-20T00:00:00+08:00','2024-08-29T00:00:00+08:00','2025-08-29T00:00:00+08:00']
    assert result['actual_fcff'] is None and result['original_failed_samples_retained']
    assert len([x for r in result['periods'] for x in r['source_inputs']])==12
    bad=copy.deepcopy(ledger);bad['reports'][0]['rows'][0]['values'][0]='4106966944.86'
    with pytest.raises(ValueError,match='row values'):verify(bad,directory,source)
    bad=copy.deepcopy(source);next(r for r in bad['records'] if r['code']=='000809' and r['period']=='2022-06-30')['bits']['FN17']+=1
    with pytest.raises(ValueError,match='inventory source bits'):verify(ledger,directory,bad)
    bad=copy.deepcopy(ledger);bad['reports'][0]['notes'][0]['anchors']=['不存在的业务依据']
    with pytest.raises(ValueError,match='business evidence'):verify(bad,directory,source)
    bad=copy.deepcopy(ledger);bad['reports'][0]['rows'][0]['section']='母公司资产负债表'
    with pytest.raises(ValueError,match='statement scope'):verify(bad,directory,source)
    # Even when a changed catalogue hash is acknowledged, late publication is rejected semantically.
    catalogue=json.loads((directory/'catalogue.json').read_bytes());a=next(a for a in catalogue['announcements'] if a['announcementId']==ledger['reports'][0]['announcement_id'])
    a['announcementTime']=int(datetime(2022,9,1,tzinfo=timezone.utc).timestamp()*1000)
    data=json.dumps(catalogue).encode();(tmp_path/'catalogue.json').write_bytes(data)
    for report in ledger['reports']:(tmp_path/report['file']).symlink_to(directory/report['file'])
    bad=copy.deepcopy(ledger);bad['catalogue_sha256']=hashlib.sha256(data).hexdigest()
    with pytest.raises(ValueError,match='not available'):verify(bad,tmp_path,source)


def test_early_commercialization_original_income_and_cutoff():
    import hashlib
    from tools.verify_tdx_business_break import verify
    directory=ROOT/'valuation/research/tdx-working-cash-forecast/review-688443';ledger=json.loads((directory/'evidence.json').read_bytes());raw=(directory.parent/'snapshot-v3.json').read_bytes();source=json.loads(raw)
    assert hashlib.sha256(raw).hexdigest()==ledger['snapshot_sha256']
    result=verify(ledger,directory,source);saved=json.loads((directory/'reconciled.json').read_bytes())
    assert result=={k:v for k,v in saved.items() if k!='evidence'}
    first,last=result['periods']
    assert first['original_available_from']==first['forecast_as_of']=='2024-09-01T00:00:00+08:00'
    assert first['pdf_half_revenue_cny']==first['income_note_cny']['other_business_income']=='12660.54'
    assert 'main_business_income' not in first['income_note_cny']  # The blank original cell is not a reported source zero.
    assert Decimal(last['income_note_cny']['main_business_income'])+Decimal(last['income_note_cny']['other_business_income'])==Decimal(last['pdf_half_revenue_cny'])
    assert result['status']=='source_values_confirmed_early_commercialization_base' and result['original_failed_samples_retained']
    bad=copy.deepcopy(ledger);bad['reports'][0]['notes'][0]['anchors']=['报告期内，公司产品已开展商业化生产与销售']
    with pytest.raises(ValueError,match='business evidence'):verify(bad,directory,source)
    bad=copy.deepcopy(ledger);bad['reports'][0]['rows'][2]['values'][0]='12660.55'
    with pytest.raises(ValueError,match='row values'):verify(bad,directory,source)
    bad=copy.deepcopy(source);next(r for r in bad['records'] if r['code']=='688443' and r['period']=='2024-03-31')['bits']['FN230']=bits(999999)
    with pytest.raises(ValueError,match='rounding bound'):verify(ledger,directory,bad)


def test_working_cash_scope_cutoff_retention_and_frozen_replay(monkeypatch):
    import tools.backtest_tdx_working_cash as m
    directory=ROOT/'valuation/research/tdx-working-cash-forecast'
    p=json.loads((directory/'protocol-v4.json').read_bytes());source=json.loads((directory/'snapshot-v3.json').read_bytes())
    result=m.study(p,source,'development');scope=result['scope_evaluation']
    saved=json.loads((directory/'development-v4-summary.json').read_bytes())
    assert all(result[k]==saved[k] for k in ('summary','by_origin','decision','scope_evaluation'))
    original=m.evaluate(dict(p,protocol_id='tdx-working-cash-forecast-v3'),source,'development')
    keys={(r['code'],r['origin']) for r in original if r['status']=='evaluated'}
    valid=[r for r in result['results'] if r['status']=='evaluated']
    refused=[r for r in result['results'] if r.get('reason')=='outside_inventory_scope']
    for model in (p['baseline'],p['benchmark'],p['candidate']):
        errors=[abs(Decimal(r['forecasts_cny'][model])-Decimal(r['actual']['working_cash_cny'])) for r in valid]
        mae=sum(e/Decimal(r['actual']['revenue_cny']) for e,r in zip(errors,valid))*100/len(valid)
        wape=sum(errors)*100/sum(abs(Decimal(r['actual']['working_cash_cny'])) for r in valid)
        assert result['summary']['models'][model]['mae_pct_actual_revenue']==pytest.approx(float(mae),abs=1e-12)
        assert result['summary']['models'][model]['wape_pct']==float(wape)
    assert scope['retained_evaluable_fraction']==len(valid)/len(keys)
    assert scope['lost_evaluable_pairs']==len(keys)-len(valid)
    assert scope['scope_refusals']==len(refused)
    assert all('forecasts_cny' not in r and 'actual' not in r for r in refused)
    assert scope['unrestricted_summary']==json.loads((directory/'development-v3-summary.json').read_bytes())['summary']
    bad=copy.deepcopy(p);bad['scope_policy']['maximum_net_inventory_to_ttm_revenue']=2
    with pytest.raises(ValueError,match='scope policy'):m.study(bad,source,'development')
    # Exercise the exact boundary before any target read, independently of real data.
    calls=[]
    def observe(*args):
        calls.append(args[-2]);return dict(working_cash_cny='10',revenue_cny='100')
    monkeypatch.setattr(m,'observation',observe)
    history=[dict(net_inventory_cny='100',revenue_cny='100')]*2
    monkeypatch.setattr(m,'inventory_signal',lambda *a:(Decimal(0),dict(history=history,boundary='test')))
    one=dict(p,samples=[p['samples'][0]],origins=[p['origins'][0]])
    assert m.evaluate(one,source,'development')[0]['status']=='evaluated' and len(calls)==2
    calls.clear();history[1]=dict(net_inventory_cny='100.01',revenue_cny='100')
    row=m.evaluate(one,source,'development')[0]
    assert row['reason']=='outside_inventory_scope' and len(calls)==1
    # An apparently perfect restricted cohort cannot pass by discarding most pairs.
    rows=copy.deepcopy(original)
    for row in rows:
        if row['status']=='evaluated':
            row['errors'][p['candidate']]=dict(cny='0',pct_actual_revenue=0)
    reduced=copy.deepcopy(rows)
    for row in reduced[len(reduced)//2:]:row.update(status='blocked',reason='outside_inventory_scope')
    monkeypatch.setattr(m,'evaluate',lambda protocol,*a:reduced if protocol['protocol_id'].endswith('v4') else rows)
    decision=m.study(p,source,'development')['decision']
    assert decision['checks']['primary_improvement'] and not decision['checks']['minimum_retained_evaluable_fraction'] and not decision['passed']


def test_working_cash_scope_source_and_holdout_selection_rejected(tmp_path,monkeypatch,capsys):
    import sys
    import tools.backtest_tdx_working_cash as m
    directory=ROOT/'valuation/research/tdx-working-cash-forecast';p=json.loads((directory/'protocol-v4.json').read_bytes())
    protocol=tmp_path/'protocol.json';snapshot=directory/'snapshot-v3.json'
    for field in ('source_protocol_sha256','source_snapshot_sha256'):
        bad=dict(p);bad[field]='0'*64;protocol.write_text(json.dumps(bad))
        monkeypatch.setattr(sys,'argv',['working_cash',str(protocol),str(snapshot),'--phase','development'])
        with pytest.raises(SystemExit) as exc:m.main()
        assert exc.value.code==1 and 'hash differs' in capsys.readouterr().out
    bad=dict(p);bad.pop('source_snapshot_sha256');protocol.write_text(json.dumps(bad))
    with pytest.raises(SystemExit):m.main()
    assert 'binding required' in capsys.readouterr().out
    monkeypatch.setattr(sys,'argv',['working_cash',str(directory/'protocol-v4.json'),str(snapshot),'--phase','holdout'])
    with pytest.raises(SystemExit):m.main()
    assert 'passing development receipt required' in capsys.readouterr().out


def test_operating_cash_forecast_source_isolation_and_development(tmp_path,monkeypatch,capsys):
    import hashlib,sys
    import tools.backtest_tdx_operating_cash as m
    directory=ROOT/'valuation/research/tdx-operating-cash-forecast';raw=(directory/'protocol.json').read_bytes();p=json.loads(raw);source=json.loads((directory/'snapshot.json').read_bytes())
    prior=ROOT/'valuation/research/tdx-capex-forecast'
    assert p['samples']==json.loads((prior/'protocol.json').read_bytes())['samples']
    assert source['study_sha256']==hashlib.sha256(raw).hexdigest()
    index={(r['code'],r['period']):r for r in source['records']}
    old=json.loads((prior/'snapshot.json').read_bytes())
    assert len(source['records'])==len(old['records'])
    for row in old['records']:
        now=index[(row['code'],row['period'])]
        assert now['artifact']==row['artifact'] and all(now['bits'][f]==b for f,b in row['bits'].items())
    result=m.study(p,source,'development');saved=json.loads((directory/'development-summary.json').read_bytes())
    assert {k:v for k,v in result.items() if k!='results'}=={k:v for k,v in saved.items() if k!='evidence'}
    assert result['decision']['passed'] and result['summary']['statuses']=={'blocked':16,'evaluated':164}
    valid=[r for r in result['results'] if r['status']=='evaluated']
    for row in valid:
        expected=Decimal(row['current']['revenue_cny'])*(Decimal(row['current']['ocf_cny'])/Decimal(row['current']['revenue_cny'])+Decimal(row['prior']['ocf_cny'])/Decimal(row['prior']['revenue_cny']))/2
        assert abs(Decimal(row['forecasts'][m.MODELS[2]]['ocf_cny'])-expected)<Decimal('.00000001')
        assert len({f['capex_cny'] for f in row['forecasts'].values()})==1
        assert row['actual_fcff'] is None
    for kind in ('ocf_cny','cash_proxy_cny'):
        for model in m.MODELS:
            errors=[abs(Decimal(r['forecasts'][model][kind])-Decimal(r['actual'][kind])) for r in valid]
            mae=sum(e/Decimal(r['actual']['revenue_cny']) for e,r in zip(errors,valid))*100/len(valid)
            wape=sum(errors)*100/sum(abs(Decimal(r['actual'][kind])) for r in valid)
            assert result['summary']['targets'][kind][model]['mae_pct_actual_revenue']==pytest.approx(float(mae),rel=1e-12)
            assert result['summary']['targets'][kind][model]['wape_pct']==float(wape)
    first=valid[0];one=dict(p,origins=[first['origin']],samples=[dict(code=first['code'],split='development')]);before=m.evaluate(one,source,'development')[0]
    changed=copy.deepcopy(source)
    for row in changed['records']:
        if row['period']>first['origin']:row['bits']['FN234']=bits(-100000)
    after=m.evaluate(one,changed,'development')[0]
    assert after['status']=='evaluated' and after['forecasts']==before['forecasts'] and after['actual']!=before['actual']
    # A forged passing receipt must not open holdout scoring.
    receipt=tmp_path/'selection.json';receipt.write_text(json.dumps(dict(saved,evidence={})))
    monkeypatch.setattr(sys,'argv',['ocf',str(directory/'protocol.json'),str(directory/'snapshot.json'),'--phase','holdout','--selection',str(receipt)])
    with pytest.raises(SystemExit) as exc:m.main()
    assert exc.value.code==1 and 'selection failed' in capsys.readouterr().out


def test_operating_cash_negative_zero_cutoff_and_cash_proxy_gate(monkeypatch):
    from collections import defaultdict
    import tools.backtest_tdx_operating_cash as m
    directory=ROOT/'valuation/research/tdx-operating-cash-forecast';p=json.loads((directory/'protocol.json').read_bytes());_,source=fixture()
    index=defaultdict(list);artifacts={a['file']:a for a in source['artifacts']};code=source['records'][0]['code']
    for row in source['records']:
        row['bits'].update(FN114=bits(int(row['period'][5:7])//3*10),FN234=bits(-10))
        index[(code,row['period'])].append(row)
    def observe():return m.observation(index,artifacts,code,date(2025,6,30),'2025-09-01T00:00:00+08:00')
    assert Decimal(observe()['ocf_cny'])==-40
    for row in source['records']:row['bits']['FN234']=bits(0)
    assert Decimal(observe()['ocf_cny'])==0
    row=index[(code,'2025-06-30')][0];row['bits']['FN314']=bits(250901)
    with pytest.raises(ValueError,match='not available'):observe()
    source=json.loads((directory/'snapshot.json').read_bytes());rows=m.evaluate(p,source,'development')
    for row in rows:
        if row['status']=='evaluated':
            row['errors']['ocf_cny'][m.MODELS[2]]=dict(cny='0',pct_actual_revenue=0)
            row['errors']['cash_proxy_cny'][m.MODELS[2]]=dict(cny='1e15',pct_actual_revenue=1e9)
    monkeypatch.setattr(m,'evaluate',lambda *a:rows)
    decision=m.study(p,source,'development')['decision']
    assert decision['checks']['primary_improvement'] and not decision['checks']['cash_proxy_nonworse'] and not decision['passed']
