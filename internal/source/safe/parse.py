"""外管局人民币中间价表：仅接受已核验的 100 港元兑人民币列。"""
import hashlib
import json
import platform
import re
import sys
from datetime import date
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path


class Table(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.targets = 0
        self.rows = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == 'table' and dict(attrs).get('id') == 'InfoTable':
            self.targets += 1
            self.active = True
        if self.active and tag == 'tr':
            self.row = []
        if self.active and tag in ('td', 'th'):
            if self.cell is not None or self.row is None:
                raise ValueError('malformed FX cell')
            self.cell = []

    def handle_data(self, text):
        if self.cell is not None:
            self.cell.append(text)

    def handle_endtag(self, tag):
        if self.active and tag in ('td', 'th'):
            self.row.append(''.join(self.cell).strip())
            self.cell = None
        if self.active and tag == 'tr':
            self.rows.append(self.row)
            self.row = None
        if self.active and tag == 'table':
            self.active = False


def parse(path):
    body = Path(path).read_bytes()
    if not 0 < len(body) <= 16*1024*1024:
        raise ValueError('empty/oversized FX page')
    p = Table()
    p.feed(body.decode('utf-8'))
    p.close()
    if p.targets != 1 or p.active or not 2 <= len(p.rows) <= 101:
        raise ValueError('missing/ambiguous FX table')
    if p.rows[0][:5] != ['日期', '美元', '欧元', '日元', '港元']:
        raise ValueError('FX column identity mismatch')
    rows = []
    for row in p.rows[1:]:
        if len(row) != len(p.rows[0]):
            raise ValueError('incomplete FX row')
        day = date.fromisoformat(row[0]).isoformat()
        raw = row[4]
        if not re.fullmatch(r'\d+(?:\.\d{1,8})?', raw) or Decimal(raw) <= 0:
            raise ValueError('invalid HKD rate')
        rows.append(dict(date=day, raw_value=raw, value=format(Decimal(raw)/100, '.12f'),
                         source_locator=f'#InfoTable/date={day}/港元'))
    days = [r['date'] for r in rows]
    if days != sorted(set(days), reverse=True):
        raise ValueError('duplicate/unordered FX dates')
    return dict(contract='alphalake-safe-hkd-cny-v1', observation_date=days[0],
                sha256=hashlib.sha256(body).hexdigest(), parser_version='safe-hkd-cny-v1',
                runtime=f'python={platform.python_version()};stdlib-htmlparser', observations=rows)


if __name__ == '__main__':
    print(json.dumps(parse(sys.argv[1]), sort_keys=True, allow_nan=False))
