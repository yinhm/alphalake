"""真实主表/附注反证与歧义零训练过滤；不恢复丢失源精度。"""
import copy
import gzip
import hashlib
import json
from collections import Counter
import subprocess
import pytest
from tools import verify_training_earnings as evidence
from tools import backtest_tdx_training_quality as quality


def test_earnings_originals_and_scope_tampering():
    result = evidence.verify()
    assert result == json.loads((evidence.DIRECTORY/'checks.json').read_bytes())
    assert Counter(r['status'] for r in result['statement_comparisons']) == {
        'matched':50,'blank_statement_source_zero_not_proven_economic_zero':10}
    for period in ('2021-06-30','2022-06-30'):
        rows = [r for r in result['statement_comparisons'] if r['code']=='600751'
                and r['period']==period and r['field'] in ('FN305','FN306')]
        assert len(rows)==2 and all(r['pdf_cny'] is None and r['source_cny']=='0' for r in rows)
        assert float(result['hna_notes'][period]['borrow_interest'])>0
        assert float(result['hna_notes'][period]['positive_interest_income'])>0
    assert result['hna_notes']['2021-12-31']['credit_note_sign_to_profit']=='-1'
    assert result['hna_notes']['2022-12-31']['credit_note_sign_to_profit']=='1'
    assert result['ttm_windows'][0]['guarantee_profit_contribution_cny']=='1751705000'
    assert result['hna_2022H1_borrow_interest_note_ttm_cny']=='-77255000'
    assert all(r['full_normalized_ebit_cny'] is None for r in result['ttm_windows'])
    ledger=json.loads((evidence.DIRECTORY/'ledger.json').read_bytes())
    bad=copy.deepcopy(ledger);bad[0]['statement']['FN86']['current_cny']='2158932001'
    with pytest.raises(ValueError,match='statement extraction'):
        evidence.verify(bad)
    bad=copy.deepcopy(ledger);bad[0]['notes']['borrow_interest']['printed_thousands'][0]='0'
    with pytest.raises(ValueError,match='note columns'):
        evidence.verify(bad)
    text=subprocess.check_output(['pdftotext','-layout',str(evidence.DIRECTORY/ledger[0]['file']),'-'],text=True)
    for unit,period in ((1,'2021-06-30'),(1000,'2021-12-31')):
        with pytest.raises(ValueError,match='unit/period'):
            evidence.statement(text,unit,period)


def test_filtered_training_replay_and_future_isolation():
    p,parent,source=quality.load_inputs()
    raw=(quality.DIRECTORY/'training-quality-result.json.gz').read_bytes()
    summary=json.loads((quality.DIRECTORY/'training-quality-summary.json').read_bytes())
    assert hashlib.sha256(raw).hexdigest()==summary['result_sha256']
    saved=json.loads(gzip.decompress(raw))
    replay=quality.study(p,parent,source)
    assert replay==saved
    assert {o:len(r) for o,r in replay['removed_training'].items()}=={'2023-06-30':66,'2024-06-30':44,'2025-06-30':40}
    assert [(r['code'],r['origin'],r['status']) for r in replay['results']]==[
        (r['code'],r['origin'],r['status']) for r in parent['results']]
    altered=copy.deepcopy(parent)
    for r in altered['results']:
        if 'actual' in r:r['actual']['ebit']+=1000000
    changed=quality.study(p,altered,source)
    assert changed['removed_training']==replay['removed_training']
    assert [(r['forecasts'],r['calibration']) for r in changed['results']]==[
        (r['forecasts'],r['calibration']) for r in replay['results']]
    assert changed['summary']!=replay['summary']
    first=parent['training']['2023-06-30']['admitted'][0]
    ref=next(r for r in first['prior']['current']['source_inputs'] if r['field']=='FN305')
    duplicate=next(r for r in source['records'] if r['code']==first['code'] and r['period']==ref['period'])
    with pytest.raises(ValueError,match='source identity'):
        quality.study(p,parent,source|dict(records=source['records']+[duplicate]))
