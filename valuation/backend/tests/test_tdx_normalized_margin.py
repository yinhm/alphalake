"""五财年范围、单年亏损与预测/实际隔离。"""
import copy
from datetime import date,timedelta
import json
from pathlib import Path
import struct

import pytest
from tools.backtest_tdx_normalized_margin import study

ROOT=Path(__file__).resolve().parents[3]
DIR=ROOT/'valuation/research/tdx-normalized-margin'


def test_five_fy_margins_keep_losses_and_future_isolation():
    p=json.loads((DIR/'protocol.json').read_text());p['samples']=[dict(code='000001',split='development')]
    def bits(v):return struct.unpack('<I',struct.pack('<f',v))[0]
    source=dict(contract_version='tdx-history-source-v1',records=[],artifacts=[])
    profits={2018:-40,2019:40,2020:80,2021:120,2022:160,2023:240,2024:320,2025:400,2026:480}
    for year,profit in profits.items():
        for quarter,(month,day) in enumerate(((3,31),(6,30),(9,30),(12,31)),1):
            end=date(year,month,day);period=end.isoformat();announced=end+timedelta(days=1)
            fields={f:bits(0) for f in ('FN305','FN306','FN83','FN82','FN301')}
            fields.update(FN230=bits(100e6),FN86=bits(profit*1e6*quarter/4),FN314=bits(int(announced.strftime('%y%m%d'))))
            source['artifacts'].append(dict(file=period,report_period=period,fetched_at='2027-09-10T00:00:00+08:00'))
            source['records'].append(dict(code='000001',period=period,artifact=period,bits=fields))
    before=study(p,source,'development');row=before['results'][0]
    assert row['status']=='evaluated' and row['base']['ebit']==200
    assert [r['year'] for r in row['history']]==[2018,2019,2020,2021,2022]
    assert row['history'][0]['margin']==-0.1
    assert row['normalized_margin']==pytest.approx(0.18)
    assert row['forecasts']['zero_growth_five_fy_mean_margin']['ebit']==pytest.approx(72)
    assert row['actual_fcff'] is None
    changed=copy.deepcopy(source)
    next(r for r in changed['records'] if r['period']=='2024-06-30')['bits']['FN230']=bits(200e6)
    after=study(p,changed,'development')['results'][0]
    assert after['forecasts']==row['forecasts'] and after['history']==row['history']
    assert after['actual']['revenue']!=row['actual']['revenue']
    missing=copy.deepcopy(source);missing['records']=[r for r in missing['records'] if r['period']!='2018-03-31']
    blocked=study(p,missing,'development')['results'][0]
    assert blocked['baseline_status']=='evaluated' and blocked['status']=='blocked_candidate'
    assert '2018-03-31' in blocked['candidate_reason']
    late=copy.deepcopy(source)
    next(r for r in late['records'] if r['period']=='2018-12-31')['bits']['FN314']=bits(240401)
    blocked=study(p,late,'development')['results'][0]
    assert blocked['status']=='blocked_candidate' and 'cutoff differs' in blocked['candidate_reason']
    with pytest.raises(ValueError,match='duplicate forecast window'):study(p|dict(windows=p['windows']*2),source,'development')
