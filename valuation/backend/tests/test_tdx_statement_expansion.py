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

RECONCILIATION = DEFAULT.parent / 'cashflow-reconciliation-2026'


def test_cashflow_reconciliation_evidence():
    assert verify(RECONCILIATION) == dict(fields=12, company_count=1, reports=3,
        positions=36, matched_nonzero=36, printed_missing_source_zero=0, pdf_cells=72)


@pytest.mark.parametrize('mutation', ['amount', 'bits', 'multiplier', 'period', 'section', 'sign', 'zero'])
def test_cashflow_reconciliation_rejects_tampering(mutation):
    evidence = json.loads((RECONCILIATION / 'evidence.json').read_text())
    row = evidence['observations'][0]
    if mutation == 'amount': row['pdf_value'] = str(float(row['pdf_value']) + 1)
    elif mutation == 'bits': row['source_bits'] ^= 1
    elif mutation == 'multiplier': row['multiplier'] = 10000
    elif mutation == 'period': row['period_basis'] = 'quarter'
    elif mutation == 'section': evidence['reports'][0]['tables']['cashflow_statement']['section'] = '母公司现金流量表'
    elif mutation == 'sign':
        row = next(r for r in evidence['observations'] if r['pdf_value'].startswith('-'))
        row['pdf_value'] = row['pdf_value'][1:]
    else: row.update(source_bits=0, pdf_value=None, status='printed_missing')
    with pytest.raises(ValueError):
        verify(RECONCILIATION, evidence=evidence)
