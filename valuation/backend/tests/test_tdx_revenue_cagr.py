import copy
from collections import defaultdict
from datetime import date
from decimal import Decimal, localcontext
import gzip
import json
from pathlib import Path

import pytest

from data_sources.alphalake import HistoricalDCFPolicy, historical_forecast
from tools import backtest_tdx_revenue_cagr as tool
from tools.tdx_research_source import source_value as value

ROOT = Path(__file__).resolve().parents[3]
PATH = ROOT/'valuation/research/tdx-revenue-cagr/protocol.json'


def test_real_replay_and_formulas():
    p, inputs = tool.load_inputs(PATH)
    result = tool.study(p, inputs['snapshot'], inputs['origin_diagnostics'])
    saved = json.loads(gzip.decompress((PATH.parent/'development-result.json.gz').read_bytes()))
    assert result == {k: v for k, v in saved.items() if k != 'evidence'}
    assert result['candidates'] == 1800
    diag = {(r['code'], r['origin']): r for r in inputs['origin_diagnostics']['results']}
    compared = 0
    for r in result['results']:
        ev = r['evidence']; d = diag[(r['code'], r['origin'])]
        if tool.MODELS[0] in r['forecasts'] and d.get('ebit_sign') == 'positive' and 0 < d.get('current_margin', 0) <= 1:
            quarters = {}
            for q in ev['current']['source_inputs'] + ev['short_history']:
                if q['status'] == 'available':
                    quarters[date.fromisoformat(q['period'])] = (Decimal(q['value_cny']), q['artifact'])
            policy = HistoricalDCFPolicy.model_validate(inputs['parent_protocol']['base_policy'] | dict(approved_report_period=r['origin']))
            annual, _ = historical_forecast(d['current']['revenue'], d['current']['ebit'], quarters, date.fromisoformat(r['origin']), policy)
            assert r['forecasts'][tool.MODELS[0]] == d['current']['revenue']*(1+annual[0].growth)
            compared += 1
        if tool.MODELS[1] in r['forecasts']:
            with localcontext() as ctx:
                ctx.prec = 40
                revenue = [sum(Decimal(q['value_cny']) for q in h['source_inputs'])/1000000 for h in ev['cagr_history']]
                growth = (revenue[0]/revenue[3])**(Decimal(1)/3)-1
                g = max(Decimal('-.1'), min(Decimal('.2'), growth))
                assert r['forecasts'][tool.MODELS[1]] == pytest.approx(float(revenue[0]*(1+g)), rel=2e-15)
    assert compared > 1200
    for m in tool.MODELS:
        rows = [r for r in result['results'] if r['status'] == 'evaluated']
        errors = [abs(Decimal(str(r['forecasts'][m]))-Decimal(str(r['actual']['value']))) for r in rows]
        independent = 100*sum(errors)/sum(Decimal(str(r['actual']['value'])) for r in rows)
        assert result['summary'][m]['wape_pct'] == pytest.approx(float(independent), rel=2e-14)
    assert result['by_profit_group']['nonpositive']['common'][tool.MODELS[1]]['n'] > 0


def test_isolation_missing_duplicate_and_hashes(tmp_path, monkeypatch):
    p, inputs = tool.load_inputs(PATH)
    source = copy.deepcopy(inputs['snapshot']); index = defaultdict(list)
    for r in source['records']:
        index[(r['code'], r['period'])].append(r)
    artifacts = {a['file']: a for a in source['artifacts']}
    end = date(2023, 6, 30); cutoff = '2023-09-01T00:00:00+08:00'
    code = next(s['code'] for s in p['samples'] if len(tool.predictions(p, index, artifacts, s['code'], end, cutoff)[0]) == 3)
    expected = tool.predictions(p, index, artifacts, code, end, cutoff)
    for r in source['records']:
        if r['period'] > end.isoformat():
            r['bits'] = {}; r['artifact'] = 'invalid-future'
        else:
            r['bits'] = {k: v for k, v in r['bits'].items() if k in ('FN230', 'FN314')}
    assert tool.predictions(p, index, artifacts, code, end, cutoff) == expected
    row = index[(code, '2023-06-30')][0]
    for profit in (0, 0xbf800000):
        row['bits']['FN86'] = profit
        assert tool.predictions(p, index, artifacts, code, end, cutoff) == expected
    index[(code, '2023-06-30')].append(copy.deepcopy(row))
    assert not tool.predictions(p, index, artifacts, code, end, cutoff)[0]
    index[(code, '2023-06-30')].pop()
    old = index.pop((code, '2020-06-30'))
    forecasts, issues, _ = tool.predictions(p, index, artifacts, code, end, cutoff)
    assert tool.MODELS[1] not in forecasts and 'incomplete' in issues[tool.MODELS[1]]
    assert tool.MODELS[0] in forecasts and tool.MODELS[2] in forecasts
    index[(code, '2020-06-30')] = old
    import struct
    row['bits']['FN314'] = struct.unpack('<I', struct.pack('<f', 230901))[0]
    assert not tool.predictions(p, index, artifacts, code, end, cutoff)[0]
    bad = tmp_path/'protocol.json'; bad.write_text(PATH.read_text().replace('"history_years": 3', '"history_years": 2'))
    with pytest.raises(ValueError, match='protocol hash'):
        tool.load_inputs(bad)
    for key in ('parent_protocol', 'snapshot'):
        target = tmp_path/p[key]; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT/p[key]).read_bytes() if key == 'parent_protocol' else b'{}')
    monkeypatch.setattr(tool, 'ROOT', tmp_path)
    with pytest.raises(ValueError, match='snapshot hash'):
        tool.load_inputs(PATH)
