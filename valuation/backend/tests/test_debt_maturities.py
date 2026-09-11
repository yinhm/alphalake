"""六家报表分量闭合不消除空白、本息混列或应付款性质缺口。"""
import copy
import pytest
from tools.verify_debt_maturities import load_inputs, verify


def test_real_maturities_and_negative_evidence():
    ledger,reports,records,artifacts=load_inputs()
    result=verify(ledger,reports,records,artifacts)
    assert result['closed_statement_totals']==12
    by_key={(r['code'],r['period']):r for r in result['results']}
    meiling=by_key['000521','2021-12-31']
    assert sum(c['classification']=='principal_interest_combined' for c in meiling['components'])==2
    assert next(c for c in meiling['components'] if '长期应付款' in c['label'])['amount_cny']=='173499.29'
    assert next(c for c in by_key['000422','2022-12-31']['components'] if '债券' in c['label'])['amount_cny'] is None
    assert all(r['full_debt'] is None and r['fully_classified_principal'] is None for r in result['results'])
    altered=copy.deepcopy(ledger)
    altered['reports'][0]['rows'][0]['amount_cny']='405591849.07'
    with pytest.raises(ValueError,match='current amount'):
        verify(altered,reports,records,artifacts)
    altered=copy.deepcopy(ledger)
    altered['reports'][0]['rows'].pop(-2) # Blank comparative-only item must also survive.
    with pytest.raises(ValueError,match='note rows'):
        verify(altered,reports,records,artifacts)
    altered=copy.deepcopy(ledger)
    altered['reports'][0]['rows'][0]['column_end']+=10
    with pytest.raises(ValueError,match='column boundary'):
        verify(altered,reports,records,artifacts)
    altered=copy.deepcopy(records)
    next(r for r in altered if r['code']=='000035' and r['period']=='2021-12-31')['bits']['FN52']^=1
    with pytest.raises(ValueError,match='source bits'):
        verify(ledger,reports,altered,artifacts)
