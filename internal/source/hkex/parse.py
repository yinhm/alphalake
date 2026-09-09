"""港交所逐日报价原文，限定已审核的安克 H 股及未复权收盘列。"""
import hashlib
import json
import platform
import re
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path


def parse(path):
    b=Path(path).read_bytes()
    if not 0<len(b)<=64*1024*1024: raise ValueError('empty/oversized HKEX page')
    text=b.decode('iso-8859-1')
    dates=re.findall(r'DATE:\s+(\d{2} [A-Z]{3} \d{4})',text)
    if len(dates)!=1: raise ValueError('ambiguous HKEX date')
    day=datetime.strptime(dates[0],'%d %b %Y').date().isoformat()
    section=text.split('<a name = "quotations">QUOTATIONS</a>')
    if len(section)!=2: raise ValueError('missing quotation section')
    section=section[1].split('<a name = "sales_all">')[0]
    if not re.search(r'CUR PRV.CLO./\s+ASK/\s+HIGH/\s+SHARES TRADED/\s+CLOSING\s+BID\s+LOW\s+TURNOVER \(\$\)',section):
        raise ValueError('unreviewed quotation columns')
    rows=re.findall(r'^\s+668 ANKER\s+HKD\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d,]+)\s*\n\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d,]+)\s*$',section,re.M)
    if len(rows)!=1: raise ValueError('missing/ambiguous traded ANKER quote')
    previous,ask,high,volume,close,bid,low,turnover=rows[0]
    if not Decimal(low)<=Decimal(close)<=Decimal(high) or Decimal(close)<=0 or int(volume.replace(',',''))<=0:
        raise ValueError('invalid close/trading evidence')
    return dict(contract='alphalake-hkex-anker-close-v1',observation_date=day,
        sha256=hashlib.sha256(b).hexdigest(),parser_version='hkex-anker-close-v1',runtime=f'python={platform.python_version()};stdlib',
        symbol='00668',currency='HKD',raw_value=close,value=format(Decimal(close),'.12f'),source_locator='#quotations/668 ANKER/second-line/CLOSING')


if __name__=='__main__': print(json.dumps(parse(sys.argv[1]),sort_keys=True,allow_nan=False))
