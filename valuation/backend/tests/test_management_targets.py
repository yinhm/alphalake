import json
from pathlib import Path
import shutil

import pytest

from tools.verify_management_targets import verify, coverage

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT/'valuation/research/management-targets-five'
PDFS = ROOT/'workspace/management-targets-five-20260911'


def test_management_targets_preserve_denominator_and_reject_tamper(tmp_path,monkeypatch):
    monkeypatch.chdir(ROOT)
    original = (SOURCE/'targets.json').read_bytes()
    if not all((PDFS/r['document']['file']).exists() for r in json.loads(original)['positions'] if 'document' in r):
        pytest.skip('original issuer PDFs absent; restore targets.json URLs/hashes')
    assert verify(SOURCE,PDFS)==json.loads((SOURCE/'coverage-result.json').read_bytes())
    copy=tmp_path/'spec';copy.mkdir()
    for name in [f.name for f in SOURCE.glob('*.json')]:
        shutil.copyfile(SOURCE/name,copy/name)
    for change in ('amount','drop_no_target','classification','concept'):
        bad=json.loads(original)
        row=next(r for r in bad['positions'] if r['targets'])
        if change=='amount':row['targets'][0]['printed_amount']='1'
        elif change=='classification':row['targets'][0]['kind']='reported_fact'
        elif change=='concept':row['targets'][0]['concept']='operating_revenue'
        else:bad['positions'].pop(0)
        (copy/'targets.json').write_text(json.dumps(bad))
        with pytest.raises(AssertionError):verify(copy,PDFS)


def test_source_coverage_stops_before_scoring_and_preserves_initial_targets():
    study=json.loads((SOURCE/'study.json').read_bytes())
    evidence=json.loads((SOURCE/'targets.json').read_bytes())
    initial=json.loads((SOURCE/'initial-targets.json').read_bytes())
    index={(r['code'],r['origin_year']):r for r in evidence['positions']}
    for row in initial['positions']:
        if row['targets']:assert row['targets']==index[row['code'],row['origin_year']]['targets']
    result=coverage(study,evidence)
    assert result['positions']==15 and result['primary_eligible_upper_bound']==5
    assert result['minimum_required']==6 and result['decision']=='stopped_insufficient_source_coverage'
    assert result['scored_positions']==0
    # 即使将来另有第六项，也只进入语义审核，不能自动声称预测有效。
    evidence['positions'][0]['targets']=[{'concept':'operating_revenue'}]
    assert coverage(study,evidence)['decision']=='pending_semantic_review'
    assert coverage(study,evidence)['scored_positions']==0
