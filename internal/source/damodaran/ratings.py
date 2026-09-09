"""仅解析 2026-01 大型非金融企业合成评级表；源区间和百分数逐字保留。"""
from html.parser import HTMLParser
from decimal import Decimal
from pathlib import Path
import hashlib
import json
import platform
import sys


class Tables(HTMLParser):
    def __init__(self):
        super().__init__(); self.tables=[]; self.rows=None; self.row=None; self.cell=None; self.text=[]
    def handle_starttag(self, tag, attrs):
        if tag=='table':
            if self.rows is not None: raise ValueError('nested table')
            self.rows=[]
        elif tag=='tr' and self.rows is not None: self.row=[]
        elif tag in ('td','th') and self.row is not None: self.cell=[]
    def handle_data(self, data):
        self.text.append(data)
        if self.cell is not None: self.cell.append(data)
    def handle_endtag(self, tag):
        if tag in ('td','th') and self.cell is not None:
            self.row.append(' '.join(''.join(self.cell).split())); self.cell=None
        elif tag=='tr' and self.row is not None:
            self.rows.append(self.row); self.row=None
        elif tag=='table' and self.rows is not None:
            self.tables.append(self.rows); self.rows=None


def parse(raw):
    p=Tables(); p.feed(raw.decode('mac_roman')); p.close()
    if 'Data used is as of January 2026' not in ' '.join(' '.join(p.text).split()):
        raise ValueError('unreviewed analysis month')
    targets=[(i,t) for i,t in enumerate(p.tables,1) if any('For large non-financial service firms' in ' '.join(r) for r in t)]
    if len(targets)!=1: raise ValueError('missing/duplicate nonfinancial table')
    table,rows=targets[0]
    if not any(r[:4]==['>','≤ to','Rating is','Spread is'] for r in rows):
        raise ValueError('unsupported boundary headers')
    ratings=['D2/D','C2/C','Ca2/CC','Caa/CCC','B3/B-','B2/B','B1/B+','Ba2/BB','Ba1/BB+','Baa2/BBB','A3/A-','A2/A','A1/A+','Aa2/AA','Aaa/AAA']
    observations=[]
    for i,row in enumerate(rows,1):
        if len(row)<4 or row[2] not in ratings: continue
        if len(row)!=9 or not row[3].endswith('%'): raise ValueError('unsupported band row')
        lo,hi=map(Decimal,row[:2]); spread=Decimal(row[3][:-1])
        if not lo.is_finite() or not hi.is_finite() or not spread.is_finite() or lo>=hi or not 0<=spread<100:
            raise ValueError('invalid band')
        observations.append(dict(lower=row[0],upper=row[1],rating=row[2],raw_value=row[3][:-1],value=f'{spread/100:.12f}',source_locator=f'table[{table}]/tr[{i}]/td[1:4]'))
    if [r['rating'] for r in observations]!=ratings: raise ValueError('incomplete rating scope')
    if any(Decimal(a['upper'])>=Decimal(b['lower']) for a,b in zip(observations,observations[1:])):
        raise ValueError('overlapping bands')
    return dict(contract='alphalake-credit-spreads-v1',observation_date='2026-01-01',sha256=hashlib.sha256(raw).hexdigest(),
                parser_version='damodaran-ratings-large-2026-v1',runtime='python='+platform.python_version(),observations=observations)


if __name__=='__main__':
    print(json.dumps(parse(Path(sys.argv[1]).read_bytes()),allow_nan=False))
