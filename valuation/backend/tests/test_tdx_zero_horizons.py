"""一年窗口与既有两/三年预测共用实现，目标值不得改变预测。"""
import copy
import json
from pathlib import Path
import struct

import pytest
from tools.validate_tdx_zero_horizons import evaluate

ROOT=Path(__file__).resolve().parents[3]
DIR=ROOT/'valuation/research/tdx-zero-growth-horizons'


def test_one_year_and_future_isolation():
    p=json.loads((DIR/'protocol.json').read_text());p['samples']=[r for r in p['samples'] if r['code']=='000829'];p['windows']=[dict(origin='2023-06-30',horizon=1)]
    source=json.loads((ROOT/p['development_snapshot']).read_text());source['records']=[r for r in source['records'] if r['code']=='000829']
    r=evaluate(p,source,'development')['results'][0]
    assert r['status']=='evaluated'
    assert r['forecasts']['zero_growth']['revenue']==r['base']['revenue']
    assert r['forecasts']['zero_growth']['ebit']==pytest.approx(r['base']['ebit'])
    assert r['forecasts']['flat_first_five']['revenue']==r['base']['revenue']*(1+r['rule_evidence']['clipped_scenario_growth'])
    assert all(len(f['annual'])==1 for f in r['forecasts'].values())
    changed=copy.deepcopy(source);target=next(v for v in changed['records'] if v['period']=='2024-06-30');target['bits']['FN230']=struct.unpack('<I',struct.pack('<f',1e12))[0]
    after=evaluate(p,changed,'development')['results'][0]
    assert r['forecasts']==after['forecasts'] and r['actual']!=after['actual']
    with pytest.raises(ValueError,match='unexpected source'):evaluate(p|dict(samples=[]),source,'development')


def test_real_development_and_independent_holdout():
    import gzip,hashlib
    from decimal import Decimal
    p=json.loads((DIR/'protocol.json').read_text())
    for phase,path,pairs,companies in [('development',ROOT/p['development_snapshot'],2527,507),('holdout',DIR/'holdout-snapshot.json',493,101)]:
        source=json.loads(path.read_text());saved=json.loads(gzip.decompress((DIR/(phase+'-result.json.gz')).read_bytes()));actual=evaluate(p,source,phase)
        assert actual=={k:v for k,v in saved.items() if k!='evidence'}
        assert saved['evidence']['snapshot_sha256']==hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual['evaluated_companies']==companies and actual['summary']['statuses']['evaluated']==pairs
        errors={m:[] for m in ('flat_first_five','zero_growth')}
        for r in actual['results']:
            if r['status']!='evaluated':continue
            base=Decimal(str(r['base']['revenue']));margin=Decimal(str(r['base']['ebit']))/base;observed=Decimal(str(r['actual']['revenue']))
            growth=Decimal(str(r['rule_evidence']['clipped_scenario_growth']))
            for model in errors:
                predicted=base*(1+growth)**r['horizon'] if model=='flat_first_five' else base
                assert float(predicted)==pytest.approx(r['forecasts'][model]['revenue'],rel=1e-12)
                assert float(predicted*margin)==pytest.approx(r['forecasts'][model]['ebit'],rel=1e-12)
                errors[model].append(abs(predicted-observed)/observed*100)
        for model,values in errors.items():assert float(sum(values)/len(values))==pytest.approx(actual['summary']['models'][model]['revenue_mae_pct'],rel=1e-12)
        assert actual['decision']['passed'] is (phase=='development')
    assert actual['decision']['checks']['each_horizon_nonworse'] is False
    assert actual['decision']['checks']['each_window_nonworse'] is False
    assert actual['by_horizon']['1']['models']['zero_growth']['revenue_mae_pct']>actual['by_horizon']['1']['models']['flat_first_five']['revenue_mae_pct']


def test_holdout_guards_before_opening_source(tmp_path,monkeypatch,capsys):
    import hashlib,sys
    import tools.validate_tdx_zero_horizons as tool
    dev=json.loads((DIR/'development-selection.json').read_text())
    monkeypatch.setattr(tool,'load',lambda p,raw: ({'artifacts':[],'source_lists':[]},dev))
    bad=tmp_path/'selection.json';bad.write_text('{}')
    source=tmp_path/'not-yet-created.json'
    monkeypatch.setattr(sys,'argv',['validate',str(DIR/'protocol.json'),'--phase','holdout','--selection',str(bad),'--snapshot',str(source)])
    with pytest.raises(SystemExit):tool.main()
    assert json.loads(capsys.readouterr().out)['reason']=='development selection differs'
    bad.write_text(json.dumps(dev));dev['decision']['passed']=False
    with pytest.raises(SystemExit):tool.main()
    assert json.loads(capsys.readouterr().out)['reason']=='passing frozen development selection required'
    dev['decision']['passed']=True;bad.write_text(json.dumps(dev));source.write_text(json.dumps(dict(study_sha256='0'*64)))
    with pytest.raises(SystemExit):tool.main()
    assert json.loads(capsys.readouterr().out)['reason']=='holdout source binding differs'
    source.write_text(json.dumps(dict(study_sha256=hashlib.sha256((DIR/'holdout-study.json').read_bytes()).hexdigest(),artifacts=['changed'],source_lists=[])))
    with pytest.raises(SystemExit):tool.main()
    assert json.loads(capsys.readouterr().out)['reason']=='holdout source versions differ'
