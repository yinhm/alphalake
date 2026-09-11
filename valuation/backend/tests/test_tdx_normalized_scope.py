"""已评分案例的独立排序、PDF定位和调整前/后收入边界。"""
import copy
from collections import defaultdict
from decimal import Decimal
import json
from pathlib import Path

import pytest
from tools.audit_tdx_normalized_scope import audit,BASE,CANDIDATE

ROOT=Path(__file__).resolve().parents[3]
DIR=ROOT/'valuation/research/tdx-normalized-margin'


def test_real_scope_review():
    config=json.loads((DIR/'scope-review.json').read_text());result=audit(config)
    saved=json.loads((DIR/'scope-result.json').read_text())
    assert result=={k:v for k,v in saved.items() if k!='evidence'}
    assert len(result['ranking'])==125
    assert result['checked_pdf_amounts']==47
    parent=json.loads((DIR/'development-result.json').read_text());gradual=json.loads((DIR/'gradual-result.json').read_text())
    actual={(r['code'],r['origin'],r['horizon']):r['actual'] for r in parent['results'] if r['status']=='evaluated'}
    independent=defaultdict(Decimal)
    for r in gradual['results']:
        if r['status']!='evaluated':continue
        observed=actual[r['code'],r['origin'],r['horizon']]
        delta={m:abs(Decimal(str(r['forecasts'][m]['ebit']))-Decimal(str(observed['ebit']))) for m in (BASE,CANDIDATE)}
        independent[r['code']]+=(delta[CANDIDATE]-delta[BASE])/Decimal(str(observed['revenue']))*100
    assert sorted(independent,key=lambda c:(-independent[c],c))==[r['code'] for r in result['ranking']]
    for r in result['ranking']:assert float(r['signed_primary_error_contribution_pp'])==pytest.approx(float(independent[r['code']]),abs=1e-10)
    assert [r['code'] for r in result['ranking'][:2]]==['000591','300672']
    cases={r['code']:r for r in result['cases']}
    for code,change in [('000591','11047151.07'),('002240','82883085.45')]:
        year=cases[code]['annual_revenue']['2021']
        assert year['status']=='source_matches_pre_restatement_not_comparable_latest'
        assert year['reported_restatement_cny']==change
    assert result['original_decision']['passed'] is False


def test_tampering_rejected(tmp_path):
    config=json.loads((DIR/'scope-review.json').read_text())
    changed=copy.deepcopy(config);changed['documents'][0]['checks'][0]['values'][0]='9236384665.75'
    with pytest.raises(ValueError,match='PDF amounts differ'):audit(changed)
    changed=copy.deepcopy(config);year=changed['documents'][0]['annual_revenue']['2021'];year['original']=year['restated']
    with pytest.raises(ValueError,match='quarter sum differs'):audit(changed)
    changed=copy.deepcopy(config);changed['inputs']['source']['sha256']='0'*64
    with pytest.raises(ValueError,match='source hash differs'):audit(changed)
    changed=copy.deepcopy(config);p=tmp_path/'modified.pdf';p.write_bytes((ROOT/changed['documents'][0]['selected_path']).read_bytes()+b'changed')
    changed['documents'][0]['selected_path']=str(p)
    with pytest.raises(ValueError,match='selected PDF hash differs'):audit(changed)
