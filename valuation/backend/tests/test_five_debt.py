"""五家真实债务证据；非流动零不等于没有到期债券。"""
import copy
import pytest
from tools.verify_five_debt import load_inputs, verify


def test_five_debt_source_values_and_current_bond_counterexample():
    ledger,source=load_inputs()
    result=verify(ledger,source)
    assert result['nonzero_matches']==40 and len(result['results'])==10
    rows={(r['code'],r['period']):r for r in result['results']}
    assert rows['000422','2021-12-31']['confirmed_current_bond_cny']=='20000000.00'
    assert rows['000599','2021-12-31']['noncurrent_bond_status']=='source_zero_matches_printed_zero'
    assert all(r['full_debt'] is None and r['economic_roic'] is None for r in rows.values())
    for field in ('FN52','FN56','FN439'):
        altered=copy.deepcopy(source)
        altered['records'][0]['bits'][field]^=1
        with pytest.raises(ValueError,match='source bits|source zero'):
            verify(ledger,altered)
    altered=copy.deepcopy(ledger)
    altered['reports'][0]['rows'][0]['line']+='1.00'
    with pytest.raises(ValueError,match='PDF row'):
        verify(altered,source)
