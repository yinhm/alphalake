"""半量为固定输入假设，不能在评分后改比例或绕过父证据。"""
from decimal import Decimal
import json
from pathlib import Path

import pytest
from tools.backtest_tdx_half_growth import forecast,run

ROOT=Path(__file__).resolve().parents[3]
DIR=ROOT/'valuation/research/tdx-half-growth'


def test_compound_fixed_half_growth_and_binding():
    for growth in (-.1,0,.2):
        for horizon in (1,2,3):
            r=forecast(dict(revenue=100,ebit=10),growth,horizon)
            expected=Decimal(100)*(1+Decimal(str(growth))/2)**horizon
            assert r['revenue']==pytest.approx(float(expected)) and r['ebit']==pytest.approx(float(expected/10))
            assert len(r['annual'])==horizon
    p=json.loads((DIR/'protocol.json').read_text())
    with pytest.raises(ValueError,match='unsupported half'):run(p|dict(growth_multiplier=.25))
    with pytest.raises(ValueError,match='parent_result hash differs'):run(p|dict(parent_result_sha256='0'*64))
