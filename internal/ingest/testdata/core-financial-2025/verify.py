"""固定样本离线对账：从原始 PDF 的指定页、项目行重新提取本期金额。"""
import csv
import hashlib
import re
from decimal import Decimal
from pathlib import Path

from pypdf import PdfReader

root = Path(__file__).resolve().parent
labels = {
    "FN8": "货币资金", "FN11": "应收账款", "FN17": "存货",
    "FN21": "流动资产合计", "FN25": "长期股权投资", "FN40": "资产总计",
    "FN41": "短期借款", "FN44": "应付账款", "FN52": "一年内到期的非流动负债",
    "FN54": "流动负债合计", "FN55": "长期借款", "FN63": "负债合计",
    "FN69": "少数股东权益", "FN72": "所有者权益合计",
    "FN114": "购建固定资产、无形资产和其他长期资产支付的现金",
    "FN133": "六、期末现金及现金等价物余额",
    "FN136": "固定资产折旧、油气资产折耗、生产性生物资产折旧",
    "FN137": "无形资产摊销", "FN138": "长期待摊费用摊销",
    "FN271": "归属于母公司所有者权益合计",
}
with (root / "values.csv").open() as f:
    rows = list(csv.DictReader(f))
assert len(rows) == 35 and {r["field"] for r in rows} == set(labels)
readers = {}
for row in rows:
    path = root / (row["pdf_url"].rsplit("/", 1)[1].removesuffix(".PDF") + ".pdf")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == row["pdf_sha256"], path
    if path not in readers:
        readers[path] = PdfReader(path)
    text = readers[path].pages[int(row["pdf_page"]) - 1].extract_text()
    label = labels[row["field"]]
    pattern = r"^\s*" + r"\s*".join(re.escape(c) for c in label) + r"\s+(-?[\d,]+\.\d{2})"
    matches = re.findall(pattern, text, re.MULTILINE)
    assert len(matches) == 1, (row, matches)
    assert Decimal(matches[0].replace(",", "")) == Decimal(row["pdf_value"]), row
print("通过：两份原始 PDF、20 个字段、35 个本期金额；页码及项目行匹配。")
