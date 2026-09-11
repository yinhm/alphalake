"""真实空白/融资分类证据与源位负向拒绝。"""
import copy
import pytest
from tools.verify_cnty_debt import load_inputs, verify


def test_cnty_debt_real_evidence_and_tampering():
    ledger,source=load_inputs()
    result=verify(ledger,source)
    assert len(result['source_matches'])==8
    assert result['additional_2022_financing_claims_cny']=='1363295731.79'
    assert result['full_debt'] is None and result['economic_roic'] is None
    altered=copy.deepcopy(ledger)
    altered['rows'][0]['values'][0]='2686120574.70'
    with pytest.raises(ValueError,match='PDF amounts'):
        verify(altered,source)
    altered=copy.deepcopy(source)
    altered['records'][0]['bits']['FN56']=1
    with pytest.raises(ValueError,match='source zero'):
        verify(ledger,altered)
    altered=copy.deepcopy(source)
    altered['records'][0]['bits']['FN52']^=1
    with pytest.raises(ValueError,match='source bits'):
        verify(ledger,altered)
    altered=copy.deepcopy(ledger)
    altered['reports'][0]['statement_page']=156 # Parent statement also has a blank bond row.
    # Scope must remain the consolidated statement even if the parent row is also blank.
    with pytest.raises(ValueError):
        verify(altered,source)
