"""重提取北交所2025年代码切换证据；不发布证券身份或改写源代码。"""
import csv
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent


class Evidence(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.rows, self.text, self.cell, self.row = [], [], None, None
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            assert self.row is None, 'nested table row'
            self.row = []
        if tag in ('td', 'th'):
            assert self.row is not None and self.cell is None, 'invalid table cell'
            self.cell = []

    def handle_data(self, data):
        self.text.append(data)
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ('td', 'th'):
            assert self.cell is not None and self.row is not None
            self.row.append(''.join(self.cell).strip())
            self.cell = None
        if tag == 'tr':
            assert self.row is not None and self.cell is None
            self.rows.append(self.row)
            self.row = None


def rebuild():
    sources = json.loads((ROOT / 'sources.json').read_text())
    evidence = {}
    for name in ('mapping', 'pilot-list', 'pilot-start', 'rollout'):
        raw = (ROOT / (name + '.html')).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == sources[name]['sha256'], name
        document = Evidence(raw.decode('utf-8'))
        assert document.row is None and document.cell is None, name
        evidence[name] = document
    rows = evidence['mapping'].rows
    assert rows[0] == ['序号', '证券简称', '上市日期', '旧代码', '新代码']
    assert len(rows) == 250, '表头、248只存量股票及日期口径脚注'
    assert rows[-1] == ['注：由全国股转系统原精选层平移进入北交所的上市公司，上表中上市日期字段为其在原精选层的挂牌日期。']
    rows = rows[:-1]
    text = {name: ''.join(''.join(doc.text).split()) for name, doc in evidence.items()}
    found = re.search(r'同时兼顾试点股票的业务覆盖度，确定(.+?)6只股票进行试点切换', text['pilot-list'])
    assert found, '试点正文名单缺失'
    pilot_names = found.group(1).split('、')
    assert len(pilot_names) == len(set(pilot_names)) == 6
    assert '2025年5月6日正式上线' in text['pilot-start']
    assert '242只存量上市公司股票继续使用现有证券代码' in text['pilot-start']
    assert '自2025年10月9日起' in text['rollout']
    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\n')
    writer.writerow(['old_code', 'new_code', 'source_name', 'source_listing_date', 'switch_date'])
    old, new, names, pilots = set(), set(), set(), set()
    for index, row in enumerate(rows[1:], 1):
        assert len(row) == 5 and row[0] == str(index), row
        _, name, listed, previous, current = row
        assert re.fullmatch(r'\d{6}', previous) and re.fullmatch(r'920\d{3}', current), row
        assert previous != current and previous not in old and current not in new and name not in names, row
        old.add(previous); new.add(current); names.add(name)
        # 试点名称只在两份交易所原文内作唯一精确连接，不用名称解析本地instrument_id。
        pilot = name in pilot_names
        if pilot:
            pilots.add(name)
        writer.writerow([previous, current, name, listed, '2025-05-06' if pilot else '2025-10-09'])
    assert pilots == set(pilot_names)
    assert ('835892' in old and '920992' in new)
    return output.getvalue().encode('utf-8')


if __name__ == '__main__':
    assert rebuild() == (ROOT / 'transitions.csv').read_bytes(), '切换表与原文提取不符'
    print('BSE: 248 official code pairs; 6 pilot / 242 later transitions; no identity publication')
