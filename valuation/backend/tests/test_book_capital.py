"""现金口径符号翻转与历史重述不得被静默抹平。"""
from decimal import Decimal
import json
import pytest
from tools import verify_book_capital as tool


def test_cash_scope_and_restatement_evidence(monkeypatch):
    result = tool.verify()
    assert result == json.loads((tool.DIRECTORY / 'book-capital-result.json').read_bytes())
    assert len(result['results']) == 12
    assert sum(len(r['source_matches']) for r in result['results']) == 48
    meiling = next(r for r in result['results'] if (r['code'], r['period']) == ('000521', '2022-12-31'))
    cases = meiling['arithmetic_capital_after_full_cash_deduction_cny']
    assert Decimal(cases['FN8']) < 0 < Decimal(cases['FN133'])
    changes = [r for r in result['comparative_checks'] if Decimal(r['later_less_original_cny'])]
    assert len(result['comparative_checks']) == 24 and len(changes) == 1
    assert changes[0]['code'] == '000546' and changes[0]['later_less_original_cny'] == '-4830069.35'
    assert all(r['economic_roic'] is None for r in result['results'])
    original = tool.subprocess.check_output
    def changed(*args, **kwargs):
        return original(*args, **kwargs).replace('-4,830,069.35', '-4,830,069.34')
    monkeypatch.setattr(tool.subprocess, 'check_output', changed)
    with pytest.raises(ValueError, match='restatement amount'):
        tool.verify()
