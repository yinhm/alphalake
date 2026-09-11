import json
from pathlib import Path
import shutil

import pytest

from tools.verify_management_targets import verify

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT/'valuation/research/management-targets-five'
PDFS = ROOT/'workspace/management-targets-five-20260911'


def test_initial_management_targets_preserve_denominator_and_reject_tamper(tmp_path,monkeypatch):
    monkeypatch.chdir(ROOT)
    original = (SOURCE/'targets.json').read_bytes()
    if not all((PDFS/r['document']['file']).exists() for r in json.loads(original)['positions'] if 'document' in r):
        pytest.skip('original issuer PDFs absent; restore targets.json URLs/hashes')
    assert verify(SOURCE,PDFS)==4
    copy=tmp_path/'spec';copy.mkdir()
    for name in ('study.json','targets.json','snapshot.json','source-receipt.json'):
        shutil.copyfile(SOURCE/name,copy/name)
    for change in ('amount','drop_pending','classification'):
        bad=json.loads(original)
        row=next(r for r in bad['positions'] if r['targets'])
        if change=='amount':row['targets'][0]['printed_amount']='1'
        elif change=='classification':row['targets'][0]['kind']='reported_fact'
        else:bad['positions'].pop(0)
        (copy/'targets.json').write_text(json.dumps(bad))
        with pytest.raises(AssertionError):verify(copy,PDFS)
