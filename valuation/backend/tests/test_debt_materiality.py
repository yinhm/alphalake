"""量级诊断保留未披露、本息混列及不同年份的补充范围。"""
from decimal import Decimal
import json
import pytest
from tools import diagnose_debt_materiality as tool


def test_verified_materiality_and_source_tampering(monkeypatch):
    result = tool.diagnose()
    assert result == json.loads((tool.maturity.DIRECTORY / 'debt-materiality-result.json').read_bytes())
    rows = {(r['code'], r['period']): r for r in result['results']}
    assert len(rows) == 12 and result['invested_capital'] is None
    r = rows['000035', '2022-12-31']
    assert Decimal(r['identified_additional_financing_claims']['amount_cny']) == Decimal('1363295731.79')
    assert 18 < Decimal(r['identified_additional_financing_claims']['percent_of_subtotal']) < 19
    assert rows['000521', '2021-12-31']['separately_reported_interest']['amount_cny'] is None
    assert rows['000599', '2022-12-31']['related_party_narrative_gap']['amount_cny'] == '2590189.25'
    for r in rows.values():
        assert sum(map(Decimal, r['source_components_cny'].values())) == Decimal(r['common_field_subtotal_cny'])
    original = tool.cnty.load_inputs
    def changed():
        ledger, source = original()
        source['records'][0]['bits']['FN439'] ^= 1
        return ledger, source
    monkeypatch.setattr(tool.cnty, 'load_inputs', changed)
    with pytest.raises(ValueError, match='source bits'):
        tool.diagnose()
