"""组合校准隔离目标公司与未来结果，保留亏损及训练不足。"""
import copy
from datetime import date
from decimal import Decimal
import gzip
import hashlib
import json
import struct
import pytest
from tools import backtest_tdx_zero_calibration as tool


def test_saved_joint_result_and_independent_coefficients():
    path=tool.ROOT/'valuation/research/tdx-zero-calibration'
    raw=(path/'development-result.json.gz').read_bytes()
    summary=json.loads((path/'development-summary.json').read_bytes())
    assert hashlib.sha256(raw).hexdigest()==summary['result_sha256']
    result=json.loads(gzip.decompress(raw))
    assert {k:v for k,v in result.items() if k not in ('results','training')} == {k:v for k,v in summary.items() if k not in ('result_sha256','training_counts')}
    assert result['evidence']['protocol_sha256']==tool.PROTOCOL_SHA
    assert len(result['results'])==1800 and not result['decision']['passed']
    for origin,pool in result['training'].items():
        assert len(pool['admitted'])+len(pool['rejected'])==600
        assert pool['realization_as_of']==f'{origin[:4]}-09-01T00:00:00+08:00'
        assert pool['forecast_as_of']==f'{int(origin[:4])-1}-09-01T00:00:00+08:00'
        assert pool['origin']==f'{int(origin[:4])-1}-06-30'
    for r in result['results']:
        pool=result['training'][r['origin']]['admitted']
        for base,model in ((tool.ZERO,tool.COMBINED),(tool.SHORT,tool.CALIBRATED)):
            coefficient=r['calibration'].get(model,{})
            if coefficient.get('status')=='nonpositive_unscaled':
                assert r['forecasts'][model]==r['forecasts'][base]
            if coefficient.get('status')!='applied':continue
            selected=[x for x in pool if x['code']!=r['code']]
            assert len(selected)==coefficient['observations']>=30
            pairs=[]
            for x in selected:
                predicted=Decimal(str(x['prior']['forecasts'][base]['ebit']))
                actual=Decimal(str(x['realized']['ebit']))
                revenue=Decimal(str(x['realized']['revenue']))
                pairs.append((actual/predicted,predicted/revenue))
            total=sum(w for _,w in pairs);weight=Decimal(0)
            for ratio,w in sorted(pairs):
                weight+=w
                if weight>=total/2:break
            multiplier=(1+max(Decimal('.5'),min(Decimal('1.5'),ratio)))/2
            assert coefficient['raw_scale']==pytest.approx(float(ratio),rel=1e-12)
            assert coefficient['multiplier']==pytest.approx(float(multiplier),rel=1e-12)
            assert r['forecasts'][model]['ebit']==pytest.approx(float(Decimal(str(r['forecasts'][base]['ebit']))*multiplier),rel=1e-12)
    valid=[r for r in result['results'] if r['status']=='evaluated']
    assert len(valid)==1726 and len({r['code'] for r in valid})==589
    for model in tool.MODELS:
        errors=[abs(Decimal(str(r['forecasts'][model]['ebit']))-Decimal(str(r['actual']['ebit']))) for r in valid]
        mae=sum(e/Decimal(str(r['actual']['revenue']))*100 for e,r in zip(errors,valid))/len(valid)
        wape=sum(errors)*100/sum(abs(Decimal(str(r['actual']['ebit']))) for r in valid)
        observed=result['summary']['models'][model]
        assert observed['ebit_mae_pct_actual_revenue']==pytest.approx(float(mae),abs=1e-10)
        assert observed['ebit_wape_pct']==pytest.approx(float(wape),abs=1e-10)
    diagnosis=json.loads((path/'diagnosis.json').read_bytes())
    assert diagnosis['source_result_sha256']==summary['result_sha256']
    for origin,pool in result['training'].items():
        for model in (tool.ZERO,tool.SHORT):
            weights=sorted((x['prior']['forecasts'][model]['ebit']/x['realized']['revenue'],x['code']) for x in pool['admitted'])
            d=diagnosis['training_weight_before_target_exclusion'][origin][model]
            assert d['largest_code']==weights[-1][1]
            assert d['largest_weight_share_pct']==pytest.approx(100*weights[-1][0]/sum(w for w,c in weights))
            assert d['top_five_weight_share_pct']==pytest.approx(100*sum(w for w,c in weights[-5:])/sum(w for w,c in weights))
    extra={}
    for r in valid:
        extra[r['code']]=extra.get(r['code'],0)+abs(r['errors'][tool.COMBINED]['ebit_error_million_cny'])-abs(r['errors'][tool.ZERO]['ebit_error_million_cny'])
    assert diagnosis['aggregate_extra_absolute_error_million_cny']==pytest.approx(sum(extra.values()))
    top=sorted(extra,key=extra.get,reverse=True)[:5]
    assert [r['code'] for r in diagnosis['largest_increases']]==top
    for r in diagnosis['largest_increases']:
        assert r['extra_absolute_error_million_cny']==pytest.approx(extra[r['code']])
    for r in diagnosis['priority_source_checks']:
        entry=next(x for x in result['training'][r['origin']]['admitted'] if x['code']==r['code'])
        assert r['prior_revenue_million_cny']==entry['prior']['current']['revenue']
        assert r['prior_ebit_proxy_million_cny']==entry['prior']['current']['ebit']
        assert r['realized_revenue_million_cny']==entry['realized']['revenue']
        assert r['realized_ebit_proxy_million_cny']==entry['realized']['ebit']


def test_target_exclusion_before_weighted_fit():
    observations = [tool.CalibrationObservation(code=f'{i:06}',predicted_ebit=100.,actual_ebit=80.,actual_revenue=1000.) for i in range(1,33)]
    observations[0] = tool.CalibrationObservation(code='000001',predicted_ebit=100000.,actual_ebit=1000000.,actual_revenue=1000.)
    own = tool.fit(observations,'000001')
    assert own['observations'] == 31 and own['excluded_target']
    assert own['raw_scale'] == .8 and own['multiplier'] == .9
    assert tool.fit(observations,'000002')['multiplier'] == 1.25
    observations[0] = observations[0].model_copy(update={'actual_ebit': -1000000.})
    assert tool.fit(observations,'000001') == own
    assert tool.fit(observations,'000002')['multiplier'] == .75
    with pytest.raises(ValueError,match='insufficient'):
        tool.fit(observations[:30],'000001')
    with pytest.raises(ValueError,match='duplicate training'):
        tool.fit(observations+[observations[0]],'000001')


def test_combination_time_loss_and_missing_training(tmp_path):
    p = json.loads((tool.ROOT/'valuation/research/tdx-zero-calibration/protocol.json').read_bytes())
    parent = dict(samples=[dict(code=f'{i:06}',split='development') for i in range(1,34)],growth_floor=-.1,growth_ceiling=.2)
    bits = lambda n: struct.unpack('<I',struct.pack('<f',n))[0]
    source = dict(contract_version='tdx-history-source-v1',records=[],artifacts=[])
    for year in range(2018,2027):
        for month, day in ((3,31),(6,30),(9,30),(12,31)):
            if year == 2026 and month > 6:
                continue
            period = date(year,month,day).isoformat()
            source['artifacts'].append(dict(file=period,report_period=period,fetched_at='2026-09-11T00:00:00Z'))
            announcement = (year-1999)*10000+430 if month==12 else (year-2000)*10000+(month+1)*100+20
            for sample in parent['samples']:
                source['records'].append(dict(code=sample['code'],period=period,artifact=period,
                    bits={**{f:bits(0) for f in ('FN82','FN83','FN301','FN305','FN306')},
                          'FN230':bits(250e6),'FN86':bits(100e6*.8**(year-2018)*month/12),'FN314':bits(announcement)}))
    expected = tool.study(p,parent,source)
    assert len(expected['results']) == 99
    first = expected['results'][0]
    assert first['status'] == 'evaluated'
    assert first['calibration'][tool.COMBINED]['observations'] == 32
    assert first['calibration'][tool.COMBINED]['multiplier'] == pytest.approx(.9,abs=1e-7)
    assert first['forecasts'][tool.COMBINED]['ebit'] == pytest.approx(first['current']['ebit']*.9,rel=1e-7)
    future = copy.deepcopy(source)
    for r in future['records']:
        if r['period'] > '2023-06-30': r['bits']['FN86'] = bits(90000e6)
    changed = tool.study(p,parent,future)
    for before,after in zip(expected['results'][:33],changed['results'][:33]):
        assert before['forecasts'] == after['forecasts'] and before['calibration'] == after['calibration']
    assert expected['training']['2023-06-30'] == changed['training']['2023-06-30']
    assert first['actual'] != changed['results'][0]['actual']
    loss = copy.deepcopy(source)
    next(r for r in loss['records'] if (r['code'],r['period'])==('000001','2023-06-30'))['bits']['FN86'] = bits(-100e6)
    row = tool.study(p,parent,loss)['results'][0]
    assert row['status'] == 'evaluated' and row['profit_group'] == 'nonpositive'
    assert row['calibration'][tool.COMBINED]['status'] == 'nonpositive_unscaled'
    assert row['forecasts'][tool.COMBINED] == row['forecasts'][tool.ZERO]
    small_parent = parent | dict(samples=parent['samples'][:30])
    small = source | dict(records=[r for r in source['records'] if r['code'] <= '000030'])
    blocked = tool.study(p,small_parent,small)
    assert all(r['status']=='calibration_blocked' for r in blocked['results'])
    assert all(tool.COMBINED not in r['forecasts'] for r in blocked['results'])
    assert blocked['independent'][tool.ZERO]['models'][tool.ZERO]['ebit_n'] == 90
    duplicate = copy.deepcopy(source)
    duplicate['records'].append(copy.deepcopy(next(r for r in duplicate['records'] if (r['code'],r['period'])==('000001','2023-06-30'))))
    rejected = tool.study(p,parent,duplicate)['results'][0]
    assert rejected['status'] == 'baseline_blocked' and not rejected['forecasts']
    path=tmp_path/'protocol.json';p['training']['weight']=1;path.write_text(json.dumps(p))
    with pytest.raises(ValueError,match='frozen protocol hash'):
        tool.load_inputs(path)
