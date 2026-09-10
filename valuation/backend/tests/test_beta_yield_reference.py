"""真实来源、冻结预期、Beta 公式与百分数转换的独立校验。"""
import hashlib
import importlib.util
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import xlrd
from data_sources.damodaran_parsers import beta_parser

ROOT = Path(__file__).resolve().parents[3]
BETA = ROOT / "internal/source/damodaran/testdata/betaGlobal.xls"
CURVE = ROOT / "internal/source/chinabond/testdata/curve.html"
spec = importlib.util.spec_from_file_location("chinabond_parser", ROOT / "internal/source/chinabond/parse.py")
curve = importlib.util.module_from_spec(spec)
spec.loader.exec_module(curve)


def test_global_beta_real_values_and_formulas():
    actual = beta_parser.alphalake_beta_snapshot(BETA)
    expected = json.loads(BETA.with_name("beta-expected.json").read_text())
    assert hashlib.sha256(BETA.read_bytes()).hexdigest() == expected["sha256"]
    actual.pop("runtime"); expected.pop("runtime")
    assert actual == expected
    wb = xlrd.open_workbook(BETA)
    ws = wb.sheet_by_name("Industry Averages")
    assert ws.cell_value(7, 5) == "Marginal"
    tax = Decimal(str(ws.cell_value(8, 5)))
    for r in range(10, 104):
        levered, de, unlevered, cash, corrected = [Decimal(str(ws.cell_value(r, c))) for c in (2, 3, 5, 6, 7)]
        assert abs(unlevered - levered / (1 + (1 - tax) * de)) < Decimal("1e-12")
        assert abs(corrected - unlevered / (1 - cash)) < Decimal("1e-12")
    by_name = {o["industry"]: o for o in actual["observations"] if o["metric_code"] == "beta_unlevered_cash_adjusted"}
    assert len(by_name) == 94
    assert by_name["Beverage (Alcoholic)"]["value"] == "0.725044343989"
    assert by_name["Electronics (Consumer & Office)"]["value"] == "1.192587261247"
    wb.release_resources()


@pytest.mark.parametrize("row,column,value", [(2, 5, "China"), (7, 5, "Effective"), (10, 0, "Aerospace/Defense"), (10, 1, 0), (10, 5, 99)])
def test_beta_rejects_layout_scope_and_semantic_mutations(monkeypatch, row, column, value):
    real = xlrd.open_workbook(BETA)
    ws = real.sheet_by_name("Industry Averages")
    original = ws.cell_value
    monkeypatch.setattr(ws, "cell_value", lambda r, c: value if (r, c) == (row, column) else original(r, c))
    monkeypatch.setattr(xlrd, "open_workbook", lambda *a, **k: SimpleNamespace(sheet_by_name=lambda name: ws, datemode=real.datemode, release_resources=lambda: None))
    with pytest.raises(ValueError):
        beta_parser.alphalake_beta_snapshot(BETA)


def test_real_curve_percent_conversion():
    actual = curve.parse_curve(CURVE)
    expected = json.loads(CURVE.with_name("expected.json").read_text())
    assert hashlib.sha256(CURVE.read_bytes()).hexdigest() == expected["sha256"]
    actual.pop("runtime"); expected.pop("runtime")
    assert actual == expected
    assert actual["observation_date"] == "2026-09-09"
    assert [o["tenor_months"] for o in actual["observations"]] == [3, 6, 12, 36, 60, 84, 120, 360]
    for o in actual["observations"]:
        assert Decimal(o["value"]) * 100 == Decimal(o["raw_value"])
    assert actual["observations"][6]["value"] == "0.016816000000"


@pytest.mark.parametrize("old,new", [("2026-09-09(%)", "2026-09-08(%)"), ("2026-09-09(%)", "2026-09-09(bp)"), ("1.6816", "NA"), ("10<!-- 年 -->年", "1<!-- 年 -->年"), ("中债国债收益率曲线</td>", "其他曲线</td>")])
def test_curve_rejects_bad_dates_units_tenors_and_identity(tmp_path, old, new):
    body = CURVE.read_text()
    assert old in body
    path = tmp_path / "bad.html"
    path.write_text(body.replace(old, new))
    with pytest.raises(ValueError):
        curve.parse_curve(path)


def test_global_capital_real_source_and_quantization():
    from data_sources.damodaran_parsers.capex_parser import alphalake_capital_snapshot
    path = ROOT / 'internal/source/damodaran/testdata/capexGlobal.xls'
    actual = alphalake_capital_snapshot(path)
    expected = json.loads(path.with_name('capital-snapshot.json').read_text())
    actual.pop('runtime'); expected.pop('runtime')
    assert actual == expected
    assert len(actual['observations']) == 94
    # 独立直接读J列，证明不是百分数，也不误用I列净资本开支/NOPAT。
    wb = xlrd.open_workbook(path); ws = wb.sheet_by_name('Industry Averages')
    for row, observation in enumerate(actual['observations'], 8):
        source = Decimal(str(ws.cell_value(row, 9)))
        assert source == Decimal(observation['raw_value'])
        assert abs(source - Decimal(observation['value'])) <= Decimal('0.0000000000005')
    assert actual['observations'][27]['industry'] == 'Electronics (Consumer & Office)'
    assert actual['observations'][27]['value'] == '1.905898248522'
    wb.release_resources()


@pytest.mark.parametrize('row,column,value', [(2,5,'China'),(8,0,'Aerospace/Defense'),(8,1,0),(8,9,0),(8,9,float('nan'))])
def test_capital_rejects_region_duplicate_sample_and_bad_ratios(monkeypatch,row,column,value):
    from data_sources.damodaran_parsers.capex_parser import alphalake_capital_snapshot
    path = ROOT / 'internal/source/damodaran/testdata/capexGlobal.xls'
    real = xlrd.open_workbook(path); ws = real.sheet_by_name('Industry Averages')
    original = ws.cell_value
    monkeypatch.setattr(ws,'cell_value',lambda r,c: value if (r,c)==(row,column) else original(r,c))
    monkeypatch.setattr(xlrd,'open_workbook',lambda *a,**k:SimpleNamespace(sheet_by_name=lambda name:ws,datemode=real.datemode,release_resources=lambda:None))
    with pytest.raises(ValueError):
        alphalake_capital_snapshot(path)
