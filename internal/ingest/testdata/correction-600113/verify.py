"""离线重新提取三份 PDF，核对更正前后单季度金额及未变的累计金额。"""
import csv
import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path

from pypdf import PdfReader

root = Path(__file__).resolve().parent
evidence = json.loads((root / "evidence.json").read_text())
pages = {}
for kind in ("original", "corrected", "notice"):
    meta = evidence[kind]
    path = root / (meta["id"] + ".pdf")
    raw = path.read_bytes()
    assert len(raw) == meta["size"], path
    assert hashlib.sha256(raw).hexdigest() == meta["sha256"], path
    pages[kind] = [re.sub(r"\s+", "", p.extract_text()) for p in PdfReader(path).pages]

with (root / "comparison.csv").open() as f:
    rows = list(csv.DictReader(f))
assert len(rows) == 4
for row in rows:
    for kind, page, version in (("original", 0, "original"), ("corrected", 0, "corrected"),
                                ("notice", 0, "original"), ("notice", 1, "corrected")):
        # 限定到项目所在行的四个表格列，避免误把累计金额当成单季度值。
        match = re.search(re.escape(row["label"]) + r"(-?[\d,]+\.\d{2})(-?\d+\.\d{2})(-?[\d,]+\.\d{2})(-?\d+\.\d{2})", pages[kind][page])
        assert match, (kind, page + 1, row["label"])
        assert Decimal(match[1].replace(",", "")) == Decimal(row[version]), (kind, row)
        assert Decimal(match[3].replace(",", "")) == Decimal(row["cumulative"]), (kind, row)
    print(f'{row["label"]}: {row["original"]} → {row["corrected"]}; 差额 {Decimal(row["corrected"]) - Decimal(row["original"])} 元')
print("通过：三份 PDF 哈希、16 个单季度单元格及 16 个累计单元格；不代表取得旧 TDX 包。")
