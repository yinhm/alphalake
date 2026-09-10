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
    assert flagged['summary']['models']['current_rule']['ebit_n']==0
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
    assert actual=={k:v for k,v in expected.items() if k!='evidence'}
    assert actual['summary']['statuses']=={'evaluated':19,'blocked':1}
    assert actual['summary']['models']['current_rule']['ebit_n']==13
    assert actual['by_split']['holdout']['models']['current_rule']['revenue_median_ape_pct']>actual['by_split']['holdout']['models']['zero_growth']['revenue_median_ape_pct']
