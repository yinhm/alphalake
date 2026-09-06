"""从完整 PDF 重提取年度、累计与期末本期值；不把派生 TTM 冒充披露值。"""
import csv
import hashlib
import re
from decimal import Decimal
from pathlib import Path

from pypdf import PdfReader

root = Path(__file__).resolve().parent
labels = {"FN230": "营业收入（元）", "FN232": "归属于上市公司股东的净利润（元）",
          "FN233": "归属于上市公司股东的扣除非经常性损益的净利润（元）",
          "FN234": "经营活动产生的现金流量净额（元）", "FN93": "减：所得税费用",
          "FN114": "购建固定资产、无形资产和其他长期资产支付的现金", "FN8": "货币资金"}
with (root / "reported.csv").open() as f:
    rows = list(csv.DictReader(f))
assert len(rows) == 38
readers = {}
for row in rows:
    name = row["pdf_url"].rsplit("/", 1)[1].removesuffix(".PDF") + ".pdf"
    path = root / name
    if name == "1224532935.pdf":
        path = root.parent / "core-financial-2025" / name
    assert hashlib.sha256(path.read_bytes()).hexdigest() == row["pdf_sha256"], path
    if path not in readers:
        readers[path] = PdfReader(path)
    text = re.sub(r"\s+", "", readers[path].pages[int(row["pdf_page"]) - 1].extract_text())
    pattern = re.escape(labels[row["field"]]) + r"(?:[一二三四五六七八九十]+、\d+)?(-?[\d,]+\.\d{2})"
    matches = re.findall(pattern, text)
    assert len(matches) == 1, (row, matches)
    assert Decimal(matches[0].replace(",", "")) == Decimal(row["pdf_value"]), row
print("通过：38 个年度/累计/期末金额均来自原始 PDF 的指定页与项目本期列。")
