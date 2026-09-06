"""离线核对财报本期列，以及万元字段在源编码前的百元级舍入。"""
import csv
import hashlib
import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from pypdf import PdfReader

root = Path(__file__).resolve().parent
labels = {"FN56": "应付债券", "FN80": "财务费用", "FN86": "三、营业利润",
          "FN92": "四、利润总额", "FN93": "减：所得税费用", "FN305": "其中：利息费用",
          "FN306": "利息收入", "FN439": "租赁负债", "FN581": "使用权资产折旧"}
with (root / "values.csv").open() as f:
    rows = list(csv.DictReader(f))
assert len(rows) == 16 and {r["field"] for r in rows} == set(labels)
readers = {}
for row in rows:
    directory = root if row["code"] == "603659" else root.parent / "core-financial-2025"
    path = directory / (row["pdf_url"].rsplit("/", 1)[1].removesuffix(".PDF") + ".pdf")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == row["pdf_sha256"], path
    if path not in readers:
        readers[path] = PdfReader(path)
    text = readers[path].pages[int(row["pdf_page"]) - 1].extract_text()
    label = labels[row["field"]]
    pattern = r"^\s*" + r"\s*".join(map(re.escape, label))
    if row["field"] == "FN56":
        pattern += r"\s+七、46"  # 合并报表的附注列，不取母公司表。
    if row["field"] in ("FN86", "FN92"):
        pattern += r"[^\d]*?"
    else:
        pattern += r"\s+"
    matches = re.findall(pattern + r"(-?[\d,]+\.\d{2})", text, re.MULTILINE)
    assert len(matches) == 1, (row, matches)
    value = Decimal(matches[0].replace(",", ""))
    assert value == Decimal(row["pdf_value"]), row
    multiplier = 10000 if row["field"] in ("FN439", "FN581") else 1
    assert int(row["multiplier"]) == multiplier, row
    encoded = (value / multiplier).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    assert encoded == Decimal(row["encoded_value"]), row
print("通过：三份完整 PDF、9 个字段、16 个本期金额及万元字段舍入口径。")
