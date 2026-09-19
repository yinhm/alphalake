"""原文/期间/单位/缺失及源位的正向与篡改拒绝。"""
import json
import pytest
from tools.verify_tdx_statement_expansion import DEFAULT, verify


def test_full_statement_evidence():
    assert verify()['matched_nonzero'] == 188


@pytest.mark.parametrize('mutation', ['amount', 'bits', 'multiplier', 'period', 'missing'])
def test_statement_evidence_rejects_tampering(mutation):
    evidence = json.loads((DEFAULT / 'evidence.json').read_text())
    row = evidence['observations'][0]
    if mutation == 'amount': row['pdf_value'] = str(float(row['pdf_value']) + 1)
    elif mutation == 'bits': row['source_bits'] ^= 1
    elif mutation == 'multiplier': row['multiplier'] *= 10000
    elif mutation == 'period': row['period_basis'] = 'quarter'
    else:
        row = next(r for r in evidence['observations'] if r['status'] == 'printed_missing')
        row.update(pdf_value='0.00', status='matched')
    with pytest.raises(ValueError):
        verify(evidence=evidence)
