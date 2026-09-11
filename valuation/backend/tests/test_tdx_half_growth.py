"""半量为固定输入假设，不能在评分后改比例或绕过父证据。"""
from decimal import Decimal
import json
from pathlib import Path

import pytest
from tools.backtest_tdx_half_growth import forecast,run

ROOT=Path(__file__).resolve().parents[3]
DIR=ROOT/'valuation/research/tdx-half-growth'


def test_compound_fixed_half_growth_and_binding():
    for growth in (-.1,0,.2):
        for horizon in (1,2,3):
            r=forecast(dict(revenue=100,ebit=10),growth,horizon)
            expected=Decimal(100)*(1+Decimal(str(growth))/2)**horizon
            assert r['revenue']==pytest.approx(float(expected)) and r['ebit']==pytest.approx(float(expected/10))
            assert len(r['annual'])==horizon
    p=json.loads((DIR/'protocol.json').read_text())
    with pytest.raises(ValueError,match='unsupported half'):run(p|dict(growth_multiplier=.25))
    with pytest.raises(ValueError,match='parent_result hash differs'):run(p|dict(parent_result_sha256='0'*64))


def test_first_year_only_keeps_new_revenue_level():
    for horizon in (1,2,3):
        r=forecast(dict(revenue=100,ebit=10),.2,horizon,growth_years=1)
        assert r['revenue']==pytest.approx(110) and r['ebit']==pytest.approx(11)
        assert [y['growth'] for y in r['annual']]==[.1]+[0]*(horizon-1)
    p=json.loads((DIR/'first-year-protocol.json').read_text())
    with pytest.raises(ValueError,match='unsupported half'):run(p|dict(growth_years=2))


def test_real_replay_and_independent_decimal():
    import gzip
    from tools.backtest_tdx_half_growth import compare
    from tools.backtest_tdx_multiyear_growth import forecast_rows
    parent=json.loads((DIR/'protocol.json').read_text())
    source_parent=json.loads((ROOT/parent['parent_protocol']).read_text())
    archived=json.loads(gzip.decompress((ROOT/parent['parent_result']).read_bytes()))
    index={(r['code'],r['origin'],r['horizon']):r for r in archived['results']}
    cases=[('protocol.json','development-result.json.gz',False),('first-year-protocol.json','first-year-development.json.gz',False),('first-year-protocol.json','first-year-holdout.json.gz',True)]
    for protocol_file,result_file,held in cases:
        p=json.loads((DIR/protocol_file).read_text());saved=json.loads(gzip.decompress((DIR/result_file).read_bytes()))
        if held:
            source=json.loads((DIR/'first-year-holdout-snapshot.json').read_text())
            inner=source_parent|dict(protocol_id='tdx-multiyear-growth-v1',samples=p['samples'],baseline='flat_first_five',benchmark='zero_growth',candidate='fade_to_terminal_by_year_five')
            actual=compare(p,dict(results=forecast_rows(inner,source,'holdout',horizons=(1,2,3))),'holdout')
        else:
            # Frozen CLI must still reject a changed engine; compatibility is a fresh
            # source replay compared with the entire archived result, not a hash bypass.
            with pytest.raises(ValueError, match='parent forecast implementation differs'):
                run(p)
            from tools.backtest_tdx_half_growth import digest
            source_raw=(ROOT/source_parent['development_snapshot']).read_bytes()
            assert digest(source_raw)==source_parent['development_snapshot_sha256']
            inner=source_parent|dict(protocol_id='tdx-multiyear-growth-v1',samples=p['samples'],baseline='flat_first_five',benchmark='zero_growth',candidate='fade_to_terminal_by_year_five')
            actual=compare(p,dict(results=forecast_rows(inner,json.loads(source_raw),'development',horizons=(1,2,3))),'development')
        assert actual=={k:v for k,v in saved.items() if k!='evidence'}
        assert actual['summary']['statuses']==({'evaluated':461,'blocked':259} if held else {'blocked':1073,'evaluated':2527})
        assert actual['decision']['passed'] is (protocol_file=='first-year-protocol.json' and not held)
        scaled=[];absolute=[];denominator=[]
        for r in actual['results']:
            if r['status']!='evaluated':continue
            original=r if held else index[r['code'],r['origin'],r['horizon']]
            g=Decimal(str(original['rule_evidence']['clipped_scenario_growth']))/2
            years=1 if p['candidate']=='first_year_half_growth' else r['horizon']
            prediction=Decimal(str(original['base']['revenue']))*(1+g)**years
            observed=Decimal(str(original['actual']['revenue']))
            assert float(prediction)==pytest.approx(r['forecasts'][p['candidate']]['revenue'],rel=1e-12)
            diff=abs(prediction-observed);scaled.append(diff/observed*100);absolute.append(diff);denominator.append(observed)
        m=actual['summary']['models'][p['candidate']]
        assert float(sum(scaled)/len(scaled))==pytest.approx(m['revenue_mae_pct'],rel=1e-12)
        assert float(sum(absolute)/sum(denominator)*100)==pytest.approx(m['revenue_wape_pct'],rel=1e-12)
    assert [k for k,v in actual['decision']['checks'].items() if not v]==['zero_growth_revenue_nonworse']


def test_frozen_selection_precedes_source_read(tmp_path,monkeypatch,capsys):
    import sys
    import tools.backtest_tdx_half_growth as tool
    dev=json.loads((DIR/'first-year-selection.json').read_text())
    monkeypatch.setattr(tool,'run',lambda p:dev)
    selection=tmp_path/'selection.json';selection.write_text('{}');source=tmp_path/'not-created.json'
    monkeypatch.setattr(sys,'argv',['half',str(DIR/'first-year-protocol.json'),'--phase','holdout','--selection',str(selection),'--snapshot',str(source)])
    with pytest.raises(SystemExit):tool.main()
    assert json.loads(capsys.readouterr().out)['reason']=='development selection differs'
    dev['decision']['passed']=False
    with pytest.raises(SystemExit):tool.main()
    assert json.loads(capsys.readouterr().out)['reason']=='passing first-year development selection required'
    dev['decision']['passed']=True;selection.write_text(json.dumps(dev));source.write_text(json.dumps(dict(study_sha256='0'*64)))
    with pytest.raises(SystemExit):tool.main()
    assert json.loads(capsys.readouterr().out)['reason']=='holdout source binding differs'
