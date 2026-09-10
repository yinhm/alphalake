"""解析四份交易所代码切换原文；来源上市日期不冒充北交所上市日。"""
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys

ROLES = ("mapping", "pilot-list", "pilot-start", "rollout")


def require(condition, detail="invalid BSE transition evidence"):
    if not condition:
        raise ValueError(detail)


class Evidence(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.rows, self.text, self.cell, self.row = [], [], None, None
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            require(self.row is None, 'nested table row')
            self.row = []
        if tag in ('td', 'th'):
            require(self.row is not None and self.cell is None, 'invalid table cell')
            self.cell = []

    def handle_data(self, data):
        self.text.append(data)
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ('td', 'th'):
            require(self.cell is not None and self.row is not None)
            self.row.append(''.join(self.cell).strip())
            self.cell = None
        if tag == 'tr':
            require(self.row is not None and self.cell is None)
            self.rows.append(self.row)
            self.row = None


def parse(paths):
    require(set(paths)==set(ROLES), 'exactly four evidence roles required')
    evidence, hashes = {}, {}
    for name in ROLES:
        path=Path(paths[name])
        require(0<path.stat().st_size<=2*1024*1024, 'evidence size outside audited scope')
        raw=path.read_bytes()
        hashes[name]=hashlib.sha256(raw).hexdigest()
        document = Evidence(raw.decode('utf-8'))
        require(document.row is None and document.cell is None, name)
        evidence[name] = document
    rows = evidence['mapping'].rows
    require(rows[0] == ['序号', '证券简称', '上市日期', '旧代码', '新代码'])
    require(len(rows) == 250, '表头、248只存量股票及日期口径脚注')
    require(rows[-1] == ['注：由全国股转系统原精选层平移进入北交所的上市公司，上表中上市日期字段为其在原精选层的挂牌日期。'])
    rows = rows[:-1]
    text = {name: ''.join(''.join(doc.text).split()) for name, doc in evidence.items()}
    found = re.search(r'同时兼顾试点股票的业务覆盖度，确定(.+?)6只股票进行试点切换', text['pilot-list'])
    require(found, '试点正文名单缺失')
    pilot_names = found.group(1).split('、')
    require(len(pilot_names) == len(set(pilot_names)) == 6)
    require('2025年5月6日正式上线' in text['pilot-start'])
    require('242只存量上市公司股票继续使用现有证券代码' in text['pilot-start'])
    require('自2025年10月9日起' in text['rollout'])
    output = []
    old, new, names, pilots = set(), set(), set(), set()
    for index, row in enumerate(rows[1:], 1):
        require(len(row) == 5 and row[0] == str(index), row)
        _, name, listed, previous, current = row
        require(re.fullmatch(r'\d{6}', previous) and re.fullmatch(r'920\d{3}', current), row)
        require(previous != current and previous not in old and current not in new and name not in names, row)
        old.add(previous); new.add(current); names.add(name)
        # 试点名称只在两份交易所原文内作唯一精确连接，不用名称解析本地instrument_id。
        pilot = name in pilot_names
        if pilot:
            pilots.add(name)
        output.append(dict(old_code=previous,new_code=current,source_name=name,source_listing_date=listed,
                           switch_date='2025-05-06' if pilot else '2025-10-09',source_row=index))
    require(pilots == set(pilot_names))
    require(('835892' in old and '920992' in new))
    return dict(contract='bse-code-transitions-2025-v1',parser_version='bse-code-transitions-2025-v1',
                runtime='python='+sys.version.split()[0],sources=hashes,transitions=output)


if __name__ == '__main__':
    print(json.dumps(parse(json.loads(sys.argv[1])),ensure_ascii=False,allow_nan=False))
