"""研发预测只用起点投入；缺研发不删基线，不伪造利润改善。"""
import copy
from datetime import date
from decimal import Decimal
import gzip
import hashlib
import json
import struct
import pytest
from tools import backtest_tdx_rd_profit as tool


def test_saved_development_arithmetic():
    directory = tool.ROOT/'valuation/research/tdx-rd-profit'
    raw = (directory/'development-result.json.gz').read_bytes()
    summary = json.loads((directory/'development-summary.json').read_bytes())
    assert hashlib.sha256(raw).hexdigest() == summary['result_sha256']
    result = json.loads(gzip.decompress(raw))
    assert {k:v for k,v in result.items() if k != 'results'} == {k:v for k,v in summary.items() if k != 'result_sha256'}
    assert result['evidence']['protocol_sha256'] == tool.PROTOCOL_SHA
    assert len(result['results']) == 1800 and not result['decision']['passed']
    valid = [r for r in result['results'] if r['status'] == 'evaluated']
    assert len(valid) == 1704 and len({r['code'] for r in valid}) == 589
    for row in result['results']:
        if not row['candidate_ready']:
            if row['forecasts']:
                assert row['forecasts'][tool.PRIMARY] == row['forecasts'][tool.BASE]
            continue
        refs = row['rd']['source_inputs']; year = int(row['origin'][:4])
        assert [r['period'] for r in refs] == [f'{year-i}-12-31' for i in range(6)]
        rd = [Decimal.from_float(struct.unpack('<f',struct.pack('<I',r['bits']))[0])/1000000 for r in refs]
        assert all(d*1000000 == Decimal(r['value_cny']) for d,r in zip(rd,refs))
        current = row['base']; prior = row['rd']['prior_operating']
        budget = (rd[0] + rd[1]*Decimal(str(current['revenue']))/Decimal(str(prior['revenue'])))/2
        ebit = Decimal(str(current['ebit'])) + rd[0] - sum(rd[1:])/5 - budget + sum(rd[:5])/5
        assert row['forecasts'][tool.PRIMARY]['ebit'] == pytest.approx(float(ebit), rel=1e-12, abs=1e-10)
    for model in tool.MODELS:
        errors = [abs(Decimal(str(r['forecasts'][model]['ebit']))-Decimal(str(r['actual']['ebit']))) for r in valid]
        mae = sum(e/Decimal(str(r['actual']['revenue']))*100 for e,r in zip(errors,valid))/len(valid)
        wape = sum(errors)*100/sum(abs(Decimal(str(r['actual']['ebit']))) for r in valid)
        observed = result['summary']['models'][model]
        assert observed['ebit_mae_pct_actual_revenue'] == pytest.approx(float(mae), abs=1e-10)
        assert observed['ebit_wape_pct'] == pytest.approx(float(wape), abs=1e-10)


def test_rd_profit_formula_time_boundary_and_missing(tmp_path, monkeypatch):
    p = json.loads((tool.ROOT/'valuation/research/tdx-rd-profit/protocol.json').read_bytes())
    parent = dict(samples=[dict(code='000001', split='development')], growth_floor=-.1, growth_ceiling=.2)
    bits = lambda n: struct.unpack('<I', struct.pack('<f', n))[0]
    source = dict(contract_version='tdx-history-source-v1', records=[], artifacts=[])
    for year in range(2017, 2026):
        for month, day in ((3,31), (6,30), (9,30), (12,31)):
            period = date(year,month,day).isoformat()
            source['artifacts'].append(dict(file=period, report_period=period, fetched_at='2026-09-11T00:00:00Z'))
            announcement = ((year-1999)*10000+430) if month == 12 else (year-2000)*10000+(month+1)*100+20
            source['records'].append(dict(code='000001',period=period,artifact=period,
                bits={**{f:bits(0) for f in ('FN82','FN83','FN301','FN305','FN306')},
                      'FN230':bits(250e6),'FN86':bits(100e6),'FN304':bits((year-2016)*10e6),'FN314':bits(announcement)}))
    result = tool.study(p,parent,source)
    first = result['results'][0]
    assert len(result['results']) == 3 and first['status'] == 'evaluated'
    assert first['forecasts'][tool.BASE]['ebit'] == 100
    assert first['forecasts'][tool.PRIMARY]['ebit'] == pytest.approx(115)
    assert first['forecasts'][tool.BUDGET]['ebit'] == pytest.approx(105)
    assert first['rd']['current_amortization'] == 30 and first['rd']['forecast_amortization'] == 40
    assert first['rd']['forecast_asset']-first['rd']['current_asset'] == first['rd']['forecast_rd']-first['rd']['forecast_amortization']
    assert not result['decision']['passed']
    for action in ('zero','missing','duplicate','late'):
        changed = copy.deepcopy(source)
        r = next(r for r in changed['records'] if r['period']=='2017-12-31')
        if action == 'zero': r['bits']['FN304'] = bits(0)
        elif action == 'missing': del r['bits']['FN304']
        elif action == 'duplicate': changed['records'].append(copy.deepcopy(r))
        else: r['bits']['FN314'] = bits(230502)
        out = tool.study(p,parent,changed)['results'][0]
        assert not out['candidate_ready'] and out['status'] == 'evaluated'
        assert out['forecasts'][tool.PRIMARY] == out['forecasts'][tool.BASE]
        assert out['candidate_fallback_reason'] and out['actual_fcff'] is None
    changed = copy.deepcopy(source)
    for r in changed['records']:
        if r['period'] > '2022-12-31':
            r['bits']['FN86'] = bits(900e6)
            r['bits']['FN304'] = 0x7fc00000
    after = tool.study(p,parent,changed)['results'][0]
    assert after['forecasts'] == first['forecasts'] and after['rd'] == first['rd']
    assert after['actual'] != first['actual']
    changed = copy.deepcopy(source)
    next(r for r in changed['records'] if r['period']=='2022-12-31')['bits']['FN86'] = bits(-100e6)
    loss = tool.study(p,parent,changed)['results'][0]
    assert loss['profit_group'] == 'nonpositive' and loss['status'] == 'evaluated'
    assert loss['forecasts'][tool.PRIMARY]['ebit'] == pytest.approx(-85)
    from tools import tdx_research_source as adapter
    renamed = copy.deepcopy(source)
    for row in renamed['records']:
        row['bits']['vendor_rd_expense'] = row['bits'].pop('FN304')
    with monkeypatch.context() as patch:
        patch.setitem(adapter.FIELDS, 'research_and_development_expense', ('vendor_rd_expense', 'ytd'))
        assert tool.study(p, parent, renamed) == result
    parent['samples'] *= 2
    with pytest.raises(ValueError,match='unique development'):
        tool.study(p,parent,source)
    path = tmp_path/'protocol.json';p['life'] = 3;path.write_text(json.dumps(p))
    with pytest.raises(ValueError,match='frozen protocol hash'):
        tool.load_inputs(path)
