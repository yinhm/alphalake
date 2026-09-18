"""安克三期处置现金：原文语义、列序及真实TDX源位；不将源零自动批准为标准零。"""
import csv
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import struct
import zipfile
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[3]


def verify(override=None):
    directory = ROOT/'internal/ingest/testdata/anker-valuation-2026'
    reports = json.loads((directory/'reports.json').read_text())
    ledger = list(csv.DictReader((directory/'reported.csv').open()))
    rows = [r for r in ledger if r['key'] == 'cash_disposals']
    assert len(rows) == 3
    output = []
    for row in rows:
        report = reports[row['pdf_id']]
        pdf = directory/report['file']
        assert hashlib.sha256(pdf.read_bytes()).hexdigest() == report['sha256']
        text = PdfReader(pdf).pages[int(row['pdf_page'])-1].extract_text()
        section = text.split(row['section'], 1)[1]
        section = section.split(row['end_section'], 1)[0]
        section = section.split('二、投资活动产生的现金流量：', 1)[1]
        compact = re.sub(r'\s+', '', section)
        value = (override or {}).get(row['period'], row['value'])
        token = '-' if float(value) == 0 else format(float(value), ',.2f')
        label = re.sub(r'\s+', '', row['label'])
        assert label+token in compact, 'current-period disposal cash differs'
        # 核对本期其他流入与小计，破折号的零解释不只依赖TDX零位。
        cash = {}
        for key in ('cash_investment_recovery','cash_investment_income','cash_other_investing_in','cash_investing_in'):
            item, = [r for r in ledger if r['period']==row['period'] and r['key']==key]
            assert item['pdf_id'] == row['pdf_id'] and item['pdf_page'] == row['pdf_page']
            pattern = re.escape(re.sub(r'\s+', '', item['label']))+r'(?:七、58\(2\))?'+re.escape(item['printed'])
            assert re.search(pattern, compact), key
            cash[key] = Decimal(item['value'])
        assert sum(cash[k] for k in ('cash_investment_recovery','cash_investment_income','cash_other_investing_in'))+Decimal(value) == cash['cash_investing_in']

        path = ROOT/'internal/ingest/testdata/valuation-chain-2026'/('gpcw'+row['period'].replace('-','')+'.zip')
        with zipfile.ZipFile(path) as archive:
            raw = archive.read(archive.namelist()[0])
        offsets = [20+11*i for i in range(struct.unpack_from('<H',raw,6)[0])]
        pos, = [p for p in offsets if raw[p:p+6] == b'300866']
        offset = struct.unpack_from('<I',raw,pos+7)[0]
        bits = struct.unpack_from('<I',raw,offset+4*109)[0]
        assert bits == struct.unpack('<I',struct.pack('<f',float(value)))[0]
        output.append(dict(period=row['period'], pdf_value_cny=value, source_bits=bits,
            pdf_sha256=report['sha256'], page=int(row['pdf_page']),
            standard_eligible=float(value)!=0, zero_status='ambiguous_source_zero' if float(value)==0 else None))
    return output


if __name__ == '__main__':
    rows = verify()
    for row in rows:
        try:
            verify({row['period']: str(float(row['pdf_value_cny'])+.01)})
        except AssertionError:
            continue
        raise AssertionError('tampering accepted')
    print(json.dumps(dict(evidence=rows, tampering_rejected=3), ensure_ascii=False, indent=2))
