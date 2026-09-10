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
assert len(reports) == 5 and len(rows) == 35
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
        if component.get("number_format") == "wrapped_integer":
            value = Decimal(re.sub(r"[,\s]", "", matches[0]))
        else:
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

assert differences == [('002959', '20251231', 305, 'interest_including_separate_lease')]
print("通过：5 份PDF、35组比较，34组源位一致；年度主表利息与附注两行加总不同，保留口径差异。")
