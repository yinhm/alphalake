"""离线重新提取合并报表原文；差异是被保护的证据，不自动改写 TDX。"""
import hashlib
import json
import re
import struct
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from pypdf import PdfReader

root = Path(__file__).resolve().parent
reports = json.loads((root / "reports.json").read_text())
rows = json.loads((root / "values.json").read_text())
assert len(reports) == 8 and len(rows) == 65
readers = {}
pages = {}
for key, report in reports.items():
    path = root / report["file"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == report["sha256"], key
    readers[key] = PdfReader(path)


def page_text(pdf, page):
    key = (pdf, page)
    if key not in pages:
        pages[key] = readers[pdf].pages[page - 1].extract_text() or ""
    return pages[key]

differences = []
for row in rows:
    total = Decimal(0)
    for component in row["components"]:
        text = page_text(component["pdf"], component["page"])
        matches = re.findall(component["pattern"], text, re.MULTILINE)
        assert len(matches) == 1, (row, matches)
        values = re.findall(r"-?[\d,]+\.\d{2}", matches[0])
        value = Decimal(values[component["column"]].replace(",", ""))
        assert value == Decimal(component["value"]), component
        assert component["sign"] in (-1, 1)
        total += value * component["sign"]
    assert total == Decimal(row["pdf_value"]), row
    encoded = (total / 10000).quantize(Decimal(".01"), rounding=ROUND_HALF_UP) if row["field"] == 439 else total
    assert encoded == Decimal(row["encoded_pdf_value"]), row
    bits = struct.unpack("<I", struct.pack("<f", float(encoded)))[0]
    equal = bits == row["source_bits"]
    assert equal == (row["comparison"] == "equal_float32"), row
    if not equal:
        differences.append((row["code"], row["period"], row["field"], row["kind"]))

assert differences == [
    ("300124", "20250630", 86, "next_year_comparative"),
    *(('603288', period, 230, 'annual_quarter_table') for period in ('20250331', '20250630', '20250930', '20251231')),
    ("603288", "20250630", 86, "next_year_comparative"),
    ("603288", "20250630", 306, "next_year_comparative"),
]
# 原文明确年度季度列经过重述。没有把旧半年报与新比较列视为同一版本。
annual = re.sub(r"\s+", "", page_text("603288-1225038237", 10))
assert "对前三季度财务数据进行了重述" in annual
print("通过：8 份原文、65 组取值/比较，58 组源位一致，7 组版本差异保留；不代表可比历史已闭合。")
