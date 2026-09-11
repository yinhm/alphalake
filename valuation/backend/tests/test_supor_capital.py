from copy import deepcopy
import json
from pathlib import Path

import pytest
from tools.verify_supor_capital import verify, DIRECTORY


def test_supor_capital_actual_source_and_tamper(monkeypatch):
    root=Path(__file__).resolve().parents[3]
    monkeypatch.chdir(root)
    ledger=json.loads((DIRECTORY/'supor-capital-evidence.json').read_bytes())
    pdfs=root/'workspace/management-targets-five-20260911'
    if not all((pdfs/r['document']['file']).exists() for r in ledger['reports']):
        pytest.skip('restore three original Supor annual PDFs from evidence URLs and hashes')
    result=verify(ledger,pdfs)
    assert result==json.loads((DIRECTORY/'supor-capital-result.json').read_bytes())
    assert result['pdf_amounts_checked']==40 and result['source_bits_checked']==9
    assert all(r['historical_fcff'] is None for r in result['results'])
    for change in ('amount','columns','field','drop_year'):
        bad=deepcopy(ledger)
        row=bad['reports'][0]['rows'][0]
        if change=='amount':row['amounts_cny'][0]='1.00'
        elif change=='columns':row['columns'].reverse()
        elif change=='field':row['tdx_field']='FN54'
        else:bad['reports'].pop()
        with pytest.raises(AssertionError):verify(bad,pdfs)
