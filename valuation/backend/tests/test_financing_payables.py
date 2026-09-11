"""融资性质识别不抹去本金/利息与关联方说明差额。"""
import pytest
from tools import verify_financing_payables as tool


def test_financing_nature_and_unreconciled_narrative(monkeypatch):
    result=tool.verify()
    rows={(r['code'],r['period']):r for r in result['results']}
    assert len(rows)==8 and result['full_debt'] is None
    assert sum(r['unclassified_current_payable_cny']=='0.00' for r in rows.values())==3
    assert rows['000599','2021-12-31']['narrative_less_current_and_noncurrent_cny']=='0.00'
    assert rows['000599','2022-12-31']['narrative_less_current_and_noncurrent_cny']=='2590189.25'
    assert rows['000599','2022-12-31']['unclassified_current_payable_cny']=='8300000.00'
    assert rows['000521','2021-12-31']['unclassified_current_payable_cny']=='173499.29'
    assert all(r['principal_interest_split'] is None for r in rows.values())
    original=tool.subprocess.check_output
    def changed(*args,**kwargs):
        text=original(*args,**kwargs)
        return text.replace('7,681,904.45','7,681,904.46')
    monkeypatch.setattr(tool.subprocess,'check_output',changed)
    with pytest.raises(ValueError,match='financing amounts'):
        tool.verify()
