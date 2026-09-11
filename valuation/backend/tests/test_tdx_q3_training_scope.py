import copy
from decimal import Decimal
import json

import pytest

from tools import audit_tdx_q3_training_scope as tool


def test_business_training_sensitivity_replays_without_deleting_evaluation_rows():
    p,inputs=tool.load_inputs()
    result=tool.study(p,inputs)
    saved=json.loads((tool.DIRECTORY/'q3-training-scope-result.json').read_text())
    assert result==saved
    original=inputs['original']
    assert [(r['code'],r['origin'],r['status']) for r in result['results']]==[
        (r['code'],r['origin'],r['status']) for r in original['results']]
    assert result['summary']['total']['statuses']=={'evaluated':170,'blocked':70}
    assert result['status']=='posthoc_training_sensitivity_not_adopted'
    assert result['diagnostic_original_gates']['passed'] is False
    assert result['business_evidence']['checked_pdf_amounts']==47
    for o,before,after in [('2023-09-30',37,36),('2024-09-30',42,41)]:
        training=result['training'][o]
        assert training['original']['fit']['training_pairs']==before
        assert training['sensitivity']['fit']['training_pairs']==after
        assert len(training['original']['observations'])==60
        assert len(training['sensitivity']['observations'])==59
        assert '002432' not in {r['code'] for r in training['sensitivity']['observations']}
        assert sum(r.get('weight_share',0) for r in training['original']['observations'])==pytest.approx(1)
    # 九安原本在开发集合，不虚构它也属于独立的120家公司评价集合。
    assert '002432' not in {r['code'] for r in result['results']}
    raw=original['calibration_training']['2023-09-30']['results']
    contributions={r['code']:Decimal(str(r['forecasts']['current_rule']['ebit']))/Decimal(str(r['actual']['revenue']))
                   for r in raw if r['status']=='evaluated'}
    share=contributions['002432']/sum(contributions.values())
    observed=next(r for r in result['training']['2023-09-30']['original']['observations'] if r['code']=='002432')['weight_share']
    assert observed==pytest.approx(float(share)) and observed>.5
    rows=[r for r in result['results'] if r['status']=='evaluated']
    mean=sum(abs(Decimal(str(r['sensitivity_ebit']))-Decimal(str(r['actual_ebit'])))
             *100/Decimal(str(r['actual_revenue'])) for r in rows)/len(rows)
    score=result['summary']['total']['models']['bias_half']['ebit_mae_pct_actual_revenue']
    assert score==pytest.approx(float(mean),rel=1e-12)
    assert score>original['summary']['total']['models']['bias_half']['ebit_mae_pct_actual_revenue']


def test_refit_rejects_forged_baseline_and_business_anchor():
    p,inputs=tool.load_inputs()
    changed=copy.deepcopy(inputs)
    changed['original']['summary']['total']['models']['bias_half']['ebit_mae_pct_actual_revenue']+=1
    with pytest.raises(ValueError,match='original Q3 results differ'):
        tool.study(p,changed)
    changed=copy.deepcopy(inputs)
    doc=next(d for d in changed['business_config']['documents'] if d['code']=='002432')
    doc['contains'][0][1]='伪造的业务证据'
    with pytest.raises(ValueError,match='business evidence missing'):
        tool.study(p,changed)
