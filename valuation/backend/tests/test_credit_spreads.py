"""归档原表与百分数转换回归。"""
import importlib.util
from pathlib import Path
from decimal import Decimal
import pytest

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('credit_parser',ROOT/'internal/source/damodaran/ratings.py')
parser=importlib.util.module_from_spec(spec);spec.loader.exec_module(parser)


def test_real_credit_table():
    raw=(ROOT/'internal/source/damodaran/testdata/ratings.html').read_bytes()
    rows=parser.parse(raw)['observations']
    # 独立抄核网页左侧大型非金融表；右侧金融表不在本次范围。
    expected=['19.00','16.00','12.61','8.85','5.09','3.21','2.75','1.84','1.38','1.11','0.89','0.78','0.70','0.55','0.40']
    assert [r['raw_value'] for r in rows]==expected
    assert [Decimal(r['value']) for r in rows]==[Decimal(v)/100 for v in expected]
    assert rows[0]['lower']=='-100000' and rows[-1]['upper']=='100000'
    for before,after in [(b'January 2026',b'January 2027'),(b'Aaa/AAA',b'UNKNOWN'),(b'19.00%',b'bad%')]:
        with pytest.raises((ValueError, ArithmeticError)):parser.parse(raw.replace(before,after))
