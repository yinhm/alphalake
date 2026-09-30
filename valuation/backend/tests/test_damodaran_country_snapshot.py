"""真实归档的独立 XML 数值核对及严格解析边界；无需网络。"""
import hashlib
import json
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import openpyxl
import pytest

from data_sources.damodaran_parsers.country_risk_parser import alphalake_country_snapshot

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "internal/source/damodaran/testdata/ctrypremJuly26.xlsx"


def test_real_country_snapshot_against_xml():
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == "8d7237c432bca23cd680f149395aa518a465d0b77bec1fdf788edc7a1748c60a"
    actual = alphalake_country_snapshot(FIXTURE)
    expected = json.loads(FIXTURE.with_name("expected-selected-v2.json").read_text())
    actual.pop("runtime")
    expected.pop("runtime")  # 环境版本影响发布签名，不影响源值预期。
    assert actual == expected
    prior = json.loads(FIXTURE.with_name("expected.json").read_text())
    assert actual['observations'][:10] == prior['observations']
    with ZipFile(FIXTURE) as z:
        ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        workbook = ET.fromstring(z.read("xl/workbook.xml"))
        sheet = next(s for s in workbook.findall("m:sheets/m:sheet", ns) if s.attrib["name"] == "ERPs by country")
        rid = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        target = next(r.attrib["Target"] for r in rels if r.attrib["Id"] == rid)
        cells = ET.fromstring(z.read("xl/" + target)).findall(".//m:c", ns)
        raw = {c.attrib["r"]: c.findtext("m:v", namespaces=ns) for c in cells}
    assert len(actual["observations"]) == 13
    for row in actual["observations"]:
        coordinate = row["source_locator"].split("!")[1]
        value = Decimal(raw[coordinate]).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)
        assert value == Decimal(row["value"])
    assert actual["observation_date"] == "2026-07-01"


@pytest.mark.parametrize("cell,value", [("D8", "renamed"), ("E3", None), ("F40", 0), ("A70", "China")])
def test_country_parser_rejects_unsupported_inputs(tmp_path, cell, value):
    # Preserve cached numbers in the test rewrite so each mutation is isolated.
    wb = openpyxl.load_workbook(FIXTURE, data_only=True)
    wb["ERPs by country"][cell] = value
    target = tmp_path / "bad.xlsx"
    wb.save(target)
    wb.close()
    with pytest.raises((ValueError, KeyError)):
        alphalake_country_snapshot(target)


def test_native_country_erp_counts_reported_crp_once(monkeypatch):
    from data_sources.damodaran_parsers.country_risk_parser import parse_country_risk
    from data_sources.damodaran_store import DamodaranStore
    from engine.segment_resolver import _get_country_erp
    from api import routes

    store = DamodaranStore(_country_risk=parse_country_risk(FIXTURE))
    monkeypatch.setattr(routes, '_get_damodaran_store', lambda: store)
    catalog = {r['name']: r for r in routes.list_erp_catalog()['countries']}
    from engine.data_dictionary import AdjustedFinancials, IndustryData
    from engine.module_2_risk import compute_cost_of_capital
    workbook = openpyxl.load_workbook(FIXTURE, data_only=True)
    try:
        sheet = workbook['ERPs by country']
        for country in ('China', 'Hong Kong', 'United States'):
            row = next(r for r in sheet.iter_rows(min_row=9) if r[0].value == country)
            total, crp = row[4].value, row[5].value
            macro = store.lookup_country(country)
            assert macro.equity_risk_premium + macro.country_risk_premium == pytest.approx(total)
            assert macro.country_risk_premium == crp
            assert _get_country_erp(country, store) == total
            assert catalog[country]['total_erp'] == total
            assert catalog[country]['base_erp'] + catalog[country]['crp'] == pytest.approx(total)
            macro.risk_free_rate = .04
            result = compute_cost_of_capital(
                AdjustedFinancials(adjusted_ebit=100, adjusted_mv_debt=0), macro,
                IndustryData(industry_name='test', region='US', beta_u=1.2),
                mv_equity=1000, book_debt=0)
            assert result.cost_of_equity == pytest.approx(.04 + 1.2 * total)
        store._country_risk['China']['total_equity_risk_premium'] = None
        with pytest.raises(ValueError, match='ERP components missing'):
            store.lookup_country('China')
        assert _get_country_erp('China', store) is None
        assert 'China' not in {r['name'] for r in routes.list_erp_catalog()['countries']}
    finally:
        workbook.close()
