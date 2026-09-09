"""严格解析中债公开页面的人民币国债八期限点；仅使用 Python 标准库。"""
import argparse
from datetime import date
from decimal import Decimal
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import platform
import re


class CurveTable(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.targets = 0
        self.rows = []
        self.row = None
        self.cell = None
        self.dates = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("id") == "gzr":
            self.dates.append(attrs.get("value"))
        if tag == "div":
            if attrs.get("id") == "gjqxData":
                self.targets += 1
                self.depth = 1
            elif self.depth:
                self.depth += 1
        if self.depth and tag == "tr":
            if self.row is not None:
                raise ValueError("nested curve row")
            self.row = []
        if self.depth and tag in ("td", "th"):
            if self.cell is not None or self.row is None:
                raise ValueError("malformed curve cell")
            self.cell = []

    def handle_data(self, data):
        self.text.append(data)
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if self.depth and tag in ("td", "th"):
            if self.cell is None:
                raise ValueError("unmatched curve cell")
            self.row.append("".join(self.cell).strip())
            self.cell = None
        if self.depth and tag == "tr":
            if self.row is None or self.cell is not None:
                raise ValueError("incomplete curve row")
            self.rows.append(self.row)
            self.row = None
        if self.depth and tag == "div":
            self.depth -= 1


def parse_curve(path):
    body = Path(path).read_bytes()
    if not body or len(body) > 16 * 1024 * 1024:
        raise ValueError("empty/oversized curve page")
    parser = CurveTable()
    parser.feed(body.decode("utf-8", errors="strict"))
    parser.close()
    if parser.targets != 1 or parser.depth or len(parser.rows) != 4 or any(len(r) != 9 for r in parser.rows):
        raise ValueError("unsupported/incomplete curve table")
    header = parser.rows[0]
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\(%\)", header[0])
    if not match or parser.dates != [match[1]]:
        raise ValueError("curve date/unit disagreement")
    day = date.fromisoformat(match[1]).isoformat()
    if header[1:] != ["3月", "6月", "1年", "3年", "5年", "7年", "10年", "30年"]:
        raise ValueError("unsupported curve tenors")
    labels = ["中债国债收益率曲线", "中债商业银行普通债收益率曲线(AAA)", "中债中短期票据收益率曲线(AAA)"]
    if [r[0] for r in parser.rows[1:]] != labels:
        raise ValueError("curve identity mismatch")
    if "在岸人民币债券市场到期收益率" not in "".join(parser.text):
        raise ValueError("missing CNY yield-to-maturity definition")
    observations = []
    for column, (tenor, raw) in enumerate(zip((3, 6, 12, 36, 60, 84, 120, 360), parser.rows[1][1:]), 2):
        if not re.fullmatch(r"-?\d+\.\d{4}", raw):
            raise ValueError("missing/non-numeric government yield")
        observations.append(dict(tenor_months=tenor, source_locator=f"#gjqxData/tr[2]/td[{column}]",
                                 raw_value=raw, value=format(Decimal(raw) / 100, ".12f")))
    return dict(contract="alphalake-cny-government-yield-v1", observation_date=day,
                sha256=hashlib.sha256(body).hexdigest(), parser_version="chinabond-government-eight-v1",
                runtime=f"python={platform.python_version()};stdlib-htmlparser", observations=observations)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AlphaLake CNY government yield snapshot")
    parser.add_argument("page", type=Path)
    print(json.dumps(parse_curve(parser.parse_args().page), sort_keys=True, allow_nan=False))
