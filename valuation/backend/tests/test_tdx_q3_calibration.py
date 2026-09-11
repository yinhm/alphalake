import copy
from datetime import date
import hashlib
import json
from pathlib import Path
import struct

import pytest

from tools import backtest_tdx_origins as tool

ROOT=Path(__file__).resolve().parents[3]
DIRECTORY=ROOT/'valuation/research/tdx-growth-expanded'


def test_q3_cutoff_keeps_h1_and_rejects_missing_or_early_dates():
    assert tool.forecast_cutoff({},date(2023,6,30))=='2023-09-01T00:00:00+08:00'
    p=json.loads((DIRECTORY/'q3-protocol.json').read_text())
    assert tool.forecast_cutoff(p,date(2023,9,30))=='2023-11-01T00:00:00+08:00'
    assert tool.forecast_cutoff(p,date(2022,9,30))=='2022-11-01T00:00:00+08:00'
    for rules in ([], {}, {'09-30':'09-30'}, {'09-30':'09-01'}, {'09-30':'02-30'}, {'09-30':None}):
        with pytest.raises(ValueError):
            tool.forecast_cutoff({'forecast_cutoff_month_days':rules},date(2023,9,30))


def test_old_h1_replay_and_q3_future_actual_does_not_change_training_or_forecasts():
    p=json.loads((DIRECTORY/'q3-protocol.json').read_text())
    raw=(ROOT/p['source_snapshot_path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==p['source_snapshot_sha256']
    source=json.loads(raw)
    for version in ('v7','v8'):
        old_p=json.loads((DIRECTORY/f'protocol-{version}.json').read_text())
        dev=tool.study(old_p,source,'development')
        old=json.loads((DIRECTORY/f'development-{version}-summary.json').read_text())
        assert dev['selection']==old['selection'] and dev['summary']==old['summary']
        held=tool.study(old_p,source,'holdout',dev)
        old=json.loads((DIRECTORY/f'holdout-{version}-summary.json').read_text())
        assert held['summary']==old['summary'] and held['validation']==old['validation']
    # 只用开发公司验证Q3日期隔离；正式120家公司评分留到本检查通过后。
    origin='2023-09-30'
    fitted=tool.fit_calibration(p,source,origin)
    assert fitted['origin']=='2022-09-30'
    assert fitted['evaluation_as_of']=='2023-11-01T00:00:00+08:00'
    before=tool.evaluate(p,source,'development',[origin],('current_rule','bias_half'),{origin:fitted})
    changed=copy.deepcopy(source)
    for row in changed['records']:
        if row['period']=='2024-09-30':
            v=struct.unpack('<f',struct.pack('<I',row['bits']['FN86']))[0]
            row['bits']['FN86']=struct.unpack('<I',struct.pack('<f',v*2))[0]
    assert tool.fit_calibration(p,changed,origin)==fitted
    after=tool.evaluate(p,changed,'development',[origin],('current_rule','bias_half'),{origin:fitted})
    assert [(r['code'],r['status']) for r in before]==[(r['code'],r['status']) for r in after]
    pairs=[(a,b) for a,b in zip(before,after) if a['status']=='evaluated']
    assert pairs
    assert all(a['forecasts']==b['forecasts'] for a,b in pairs)
    assert any(a['errors']!=b['errors'] for a,b in pairs)


def test_real_q3_result_keeps_denominator_and_failed_gate():
    import gzip
    from decimal import Decimal
    p_raw=(DIRECTORY/'q3-protocol.json').read_bytes();p=json.loads(p_raw)
    source=json.loads((ROOT/p['source_snapshot_path']).read_bytes())
    saved=json.loads(gzip.decompress((DIRECTORY/'q3-holdout.json.gz').read_bytes()))
    assert saved['evidence']['protocol_sha256']==hashlib.sha256(p_raw).hexdigest()
    for name, sha in p['frozen_parent_receipts'].items():
        assert hashlib.sha256((DIRECTORY/name).read_bytes()).hexdigest()==sha
    dev=tool.study(p,source,'development')
    result=tool.study(p,source,'holdout',dev)
    assert result=={k:v for k,v in saved.items() if k!='evidence'}
    assert result['summary']['total']['statuses']=={'evaluated':170,'blocked':70}
    assert len({r['code'] for r in result['results']})==120
    assert {k for k,v in result['validation']['verdict']['checks'].items() if not v}=={'primary_improvement'}
    assert result['validation']['verdict']['passed'] is False
    for phase, result in [('development',dev),('holdout',result)]:
        summary=copy.deepcopy(result);summary.pop('results')
        for training in summary['calibration_training'].values():training.pop('results')
        old=json.loads((DIRECTORY/f'q3-{phase}-summary.json').read_text())
        assert summary=={k:v for k,v in old.items() if k!='evidence'}
    # 由保存的逐公司实际/预测金额独立汇总，保留未来负利润。
    rows=[r for r in saved['results'] if r['status']=='evaluated']
    assert any(r['actual']['ebit']<0 for r in rows)
    for model in ('current_rule','zero_growth','bias_half'):
        total=sum(abs(Decimal(str(r['forecasts'][model]['ebit']))-Decimal(str(r['actual']['ebit'])))
                  *100/Decimal(str(r['actual']['revenue'])) for r in rows)/len(rows)
        expected=saved['summary']['total']['models'][model]['ebit_mae_pct_actual_revenue']
        assert float(total)==pytest.approx(expected,rel=1e-12,abs=1e-12)
