"""恢复候选允许起点/实际亏损，但缺历史仍保持阻断。"""
import copy
from datetime import date,timedelta
import json
from pathlib import Path
import struct

import pytest
from tools.backtest_tdx_loss_recovery import study,MODELS

ROOT=Path(__file__).resolve().parents[3]
DIR=ROOT/'valuation/research/tdx-loss-recovery'


def test_loss_path_and_outcome_isolation():
    p=json.loads((DIR/'protocol.json').read_text());p['samples']=[dict(code='000001',split='development')]
    def bits(n):return struct.unpack('<I',struct.pack('<f',n))[0]
    source=dict(contract_version='tdx-history-source-v1',records=[],artifacts=[])
    for year,profit in {2018:-40,2019:40,2020:80,2021:120,2022:160,2023:-240,2024:-320,2025:400,2026:480}.items():
        for q,(month,day) in enumerate(((3,31),(6,30),(9,30),(12,31)),1):
            end=date(year,month,day);period=end.isoformat()
            fields={f:bits(0) for f in ('FN305','FN306','FN83','FN82','FN301')}
            fields.update(FN230=bits(100e6),FN86=bits(profit*1e6*q/4),FN314=bits(int((end+timedelta(days=1)).strftime('%y%m%d'))))
            source['records'].append(dict(code='000001',period=period,artifact=period,bits=fields))
            source['artifacts'].append(dict(file=period,report_period=period,fetched_at='2027-09-10T00:00:00+08:00'))
    result=study(p,source);r=result['results'][0]
    assert r['status']=='evaluated' and r['base']['ebit']==-40 and r['actual']['ebit']==-280
    assert r['normalized_margin']==pytest.approx(.18)
    assert [v['forecasts'][MODELS[1]]['ebit'] for v in result['results'][:3]]==pytest.approx([-17.6,4.8,27.2])
    assert r['forecasts'][MODELS[2]]['ebit']==0 and r['actual_fcff'] is None
    changed=copy.deepcopy(source);next(v for v in changed['records'] if v['period']=='2024-06-30')['bits']['FN86']=bits(400e6)
    after=study(p,changed)['results'][0]
    assert after['actual']['ebit']>0 and after['forecasts']==r['forecasts'] and after['history']==r['history']
    changed=copy.deepcopy(source);changed['records']=[v for v in changed['records'] if v['period']!='2018-03-31']
    after=study(p,changed)['results'][0]
    assert after['status']=='blocked_candidate' and after['baseline_status']=='evaluated'
    changed=copy.deepcopy(source);next(v for v in changed['records'] if v['period']=='2018-12-31')['bits']['FN314']=bits(240401)
    assert study(p,changed)['results'][0]['status']=='blocked_candidate'
    with pytest.raises(ValueError,match='unexpected source'):study(p|dict(samples=[dict(code='000002',split='development')]),source)
    with pytest.raises(ValueError,match='duplicate window'):study(p|dict(windows=p['windows']*2),source)


def test_real_replay_decimal_and_source_binding(tmp_path):
    from decimal import Decimal
    import gzip,hashlib,subprocess,sys
    from tools.backtest_tdx_history import value,EBIT
    p=json.loads((DIR/'protocol.json').read_text());source=json.loads((DIR/'development-snapshot.json').read_text())
    saved=json.loads(gzip.decompress((DIR/'development-result.json.gz').read_bytes()));actual=study(p,source)
    assert actual=={k:v for k,v in saved.items() if k!='evidence'}
    assert hashlib.sha256((DIR/'development-snapshot.json').read_bytes()).hexdigest()==saved['evidence']['snapshot_sha256']
    assert actual['baseline_evaluable']==834 and actual['evaluated_companies']==82
    assert actual['summary']['statuses']=={'blocked':178,'blocked_candidate':617,'outside_loss_scope':2588,'evaluated':217}
    index={(r['code'],r['period']):r for r in source['records']};scaled=[];absolute=[];denominator=[]
    valid=[r for r in actual['results'] if r['status']=='evaluated']
    assert any(r['actual']['ebit']>0 for r in valid) and any(r['actual']['ebit']<=0 for r in valid)
    for r in valid:
        margins=[]
        for year in range(int(r['origin'][:4])-5,int(r['origin'][:4])):
            revenue=sum(value(index[r['code'],str(year)+'-'+suffix],'FN230') for suffix in ('03-31','06-30','09-30','12-31'))
            ebit=sum(value(index[r['code'],str(year)+'-12-31'],field)*sign for field,sign in EBIT.items())
            margins.append(ebit/revenue)
        normal=sum(margins)/5
        current=Decimal(str(r['base']['ebit']))/Decimal(str(r['base']['revenue']))
        predicted=Decimal(str(r['base']['revenue']))*(current+(normal-current)*r['horizon']/5)
        assert float(predicted)==pytest.approx(r['forecasts'][MODELS[1]]['ebit'],rel=1e-11,abs=1e-9)
        delta=abs(predicted-Decimal(str(r['actual']['ebit'])))
        absolute.append(delta);scaled.append(delta/Decimal(str(r['actual']['revenue'])));denominator.append(abs(Decimal(str(r['actual']['ebit']))))
    m=actual['summary']['models'][MODELS[1]]
    assert m['ebit_mae_pct_actual_revenue']==pytest.approx(float(sum(scaled)/len(scaled)*100),rel=1e-12)
    assert m['ebit_wape_pct']==pytest.approx(float(sum(absolute)/sum(denominator)*100),rel=1e-12)
    assert actual['decision']['passed'] is False and actual['decision']['checks']['minimum_sample'] is True
    source['study_sha256']='0'*64;bad=tmp_path/'bad.json';bad.write_text(json.dumps(source))
    run=subprocess.run([sys.executable,'-m','tools.backtest_tdx_loss_recovery',str(DIR/'protocol.json'),str(bad)],capture_output=True,text=True)
    assert run.returncode==1 and json.loads(run.stdout)['reason']=='source binding differs'
