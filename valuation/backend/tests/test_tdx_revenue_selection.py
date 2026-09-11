import copy
from collections import defaultdict
from datetime import date
from decimal import Decimal
import gzip
import json
from pathlib import Path
import struct

import pytest
from tools import backtest_tdx_revenue_selection as tool

ROOT = Path(__file__).resolve().parents[3]
PATH = ROOT/'valuation/research/tdx-revenue-selection/protocol.json'


def test_real_replay_and_independent_selection():
    p, base, inputs = tool.load_inputs(PATH)
    actual = tool.study(p, base, inputs['snapshot'], inputs['origin_diagnostics'])
    saved = json.loads(gzip.decompress((PATH.parent/'development-result.json.gz').read_bytes()))
    assert actual == {k: v for k, v in saved.items() if k != 'evidence'}
    assert actual['candidates'] == 1800 and actual['summary'][tool.MODELS[1]]['n'] == 1726
    assert [k for k, v in actual['decision']['checks'].items() if not v] == ['primary_improvement', 'zero_growth_nonworse']
    valid = [r for r in actual['results'] if r['status'] == 'evaluated']
    assert len(valid) == actual['independent'][tool.MODELS[0]]['n']
    for r in actual['results']:
        pairs = [h for h in r['history'] if h['status'] == 'evaluated']
        if r['selection'] == 'past_error_choice':
            assert len(pairs) >= 2
            losses = {m: sum(abs(Decimal(str(h['forecasts'][m]))-Decimal(str(h['actual']['value'])))/Decimal(str(h['actual']['value'])) for h in pairs)/len(pairs) for m in (tool.MODELS[0], tool.MODELS[2])}
            choice = tool.MODELS[0] if losses[tool.MODELS[0]] < losses[tool.MODELS[2]] else tool.MODELS[2]
            assert r['choice'] == choice
        elif r['selection'] == 'insufficient_matured_history_use_baseline':
            assert len(pairs) < 2 and r['choice'] == tool.MODELS[0]
        if 'choice' in r:
            assert r['forecasts'][tool.MODELS[1]] == r['forecasts'][r['choice']]
        for h in r['history']:
            assert h['origin'] < h['target'] <= r['origin'] < r['target']
            assert h['actual_as_of'] == r['origin'][:4]+'-09-01T00:00:00+08:00'
    for m in tool.MODELS:
        numerator = sum(abs(Decimal(str(r['forecasts'][m]))-Decimal(str(r['actual']['value']))) for r in valid)
        denominator = sum(Decimal(str(r['actual']['value'])) for r in valid)
        assert actual['summary'][m]['wape_pct'] == pytest.approx(float(100*numerator/denominator), rel=2e-14)
    assert actual['by_profit_group']['nonpositive']['common'][tool.MODELS[1]]['n'] > 0


def test_future_missing_dates_profit_and_tie(tmp_path, monkeypatch):
    p, base, inputs = tool.load_inputs(PATH)
    source = copy.deepcopy(inputs['snapshot']); index = defaultdict(list)
    for r in source['records']:
        index[(r['code'], r['period'])].append(r)
    artifacts = {a['file']: a for a in source['artifacts']}
    end = date(2023, 6, 30)
    code = next(s['code'] for s in base['samples'] if tool.select(p, base, index, artifacts, s['code'], end).get('matured_pairs') == 3)
    expected = tool.select(p, base, index, artifacts, code, end)
    for r in source['records']:
        if r['period'] > end.isoformat():
            r['bits'] = {}; r['artifact'] = 'invalid-future'
        else:
            r['bits'] = {k: v for k, v in r['bits'].items() if k in ('FN230', 'FN314')}
    assert tool.select(p, base, index, artifacts, code, end) == expected
    for r in source['records']:
        if r['period'] <= end.isoformat():
            r['bits']['FN86'] = struct.unpack('<I', struct.pack('<f', -100))[0]
    assert tool.select(p, base, index, artifacts, code, end) == expected
    recent = defaultdict(list, {k: v for k, v in index.items() if k[1] >= '2021-07-01'})
    fallback = tool.select(p, base, recent, artifacts, code, end)
    assert fallback['selection'] == 'insufficient_matured_history_use_baseline'
    assert fallback['forecasts'][tool.MODELS[1]] == expected['forecasts'][tool.MODELS[0]]
    index[(code, '2021-06-30')][0]['bits']['FN314'] = struct.unpack('<I', struct.pack('<f', 230901))[0]
    changed = tool.select(p, base, index, artifacts, code, end)
    assert changed['matured_pairs'] < 3
    assert any('unavailable_at_cutoff' in h.get('reason', '') or h['issues'] for h in changed['history'])
    bad = tmp_path/'protocol.json'; bad.write_text(PATH.read_text().replace('"minimum_matured_pairs": 2', '"minimum_matured_pairs": 1'))
    with pytest.raises(ValueError, match='protocol hash'):
        tool.load_inputs(bad)
    monkeypatch.setattr(tool, 'inputs_at', lambda *args: ({tool.MODELS[0]:100., tool.MODELS[2]:100.}, {}, {}))
    monkeypatch.setattr(tool.parent, 'revenue', lambda *args: {'value':100.})
    tied = tool.select(p, base, index, artifacts, code, end)
    assert tied['choice'] == tool.MODELS[2] and tied['matured_pairs'] == 3
