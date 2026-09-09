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
    expected = json.loads(FIXTURE.with_name("expected.json").read_text())
    actual.pop("runtime")
    expected.pop("runtime")  # 环境版本影响发布签名，不影响源值预期。
    assert actual == expected
    with ZipFile(FIXTURE) as z:
        ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        workbook = ET.fromstring(z.read("xl/workbook.xml"))
        sheet = next(s for s in workbook.findall("m:sheets/m:sheet", ns) if s.attrib["name"] == "ERPs by country")
        rid = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        target = next(r.attrib["Target"] for r in rels if r.attrib["Id"] == rid)
        cells = ET.fromstring(z.read("xl/" + target)).findall(".//m:c", ns)
        raw = {c.attrib["r"]: c.findtext("m:v", namespaces=ns) for c in cells}
    assert len(actual["observations"]) == 10
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
