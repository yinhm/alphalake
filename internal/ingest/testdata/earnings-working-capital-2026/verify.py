"""复用已重新提取的 PDF 台账，锁定新增 FN 的字段/期间/原文和源位证据。"""
import csv
import hashlib
import json
from decimal import Decimal as D, ROUND_HALF_UP
import io
from pathlib import Path
import struct
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parent
FIELDS = {'FN12': ('prepayments', 'prepayments'), 'FN13': ('other_receivables', 'other_receivable'),
          'FN46': ('payroll', 'payroll_payable'), 'FN47': ('tax_payable', 'tax_payable'),
          'FN82': ('fair_value_income', 'fair_value_income'), 'FN83': ('investment_income', 'investment_income'),
          'FN301': ('disposal_income', 'disposal_income')}


def verify(write=False, root=ROOT, fields=FIELDS, expected_count=38, field_options=None):
    output = []
    aliases = {'FY2025': '2025-12-31', 'H12025': '2025-06-30', 'H12026': '2026-06-30'}
    for idx, (company, code) in enumerate([('anker', '300866'), ('moutai', '600519')]):
        directory = root.parent / (company + '-valuation-2026')
        with (directory / 'reported.csv').open() as f:
            ledger = list(csv.DictReader(f))
        if company == 'anker' and field_options is not None:
            for e in json.loads((root.parent/'anker-dcf-2026/evidence.json').read_text()):
                for c in e['cells']:
                    ledger.append(dict(id='2026-06-30/extra_'+c['key'],value=c['value'],pdf_id=e['pdf_id'],pdf_page=e['pdf_page']))
        for field, keys in fields.items():
            for row in ledger:
                period, key = row['id'].split('/', 1)
                period = aliases.get(period, period)
                if key != keys[idx] or period not in aliases.values():
                    continue
                pdf = directory / (row['pdf_id'] + '.pdf')
                with zipfile.ZipFile(root.parent / 'valuation-chain-2026' / ('gpcw' + period.replace('-', '') + '.zip')) as archive:
                    data = archive.read(archive.namelist()[0])
                records = [20 + n * 11 for n in range(struct.unpack_from('<H', data, 6)[0])]
                pos, = [p for p in records if data[p:p+6].decode() == code]
                offset = struct.unpack_from('<I', data, pos+7)[0]
                bits = struct.unpack_from('<I', data, offset+4*(int(field[2:])-1))[0]
                basis, multiplier = field_options[field] if field_options else ('instant' if int(field[2:]) < 80 else 'ytd', 1)
                encoded = (D(row['value'])/multiplier).quantize(D('.01'), rounding=ROUND_HALF_UP)
                assert struct.pack('<I', bits) == struct.pack('<f', float(encoded)), (code, period, field)
                output.append(dict(code=code, field=field, period=period, item=key,
                    period_basis=basis, pdf_value=row['value'],
                    pdf_page=row['pdf_page'], pdf_path='../'+directory.name+'/'+pdf.name,
                    pdf_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest()))
                if field_options is not None:
                    output[-1].update(value_multiplier=multiplier, source_value=str(encoded))
    assert len(output) == expected_count and {r['field'] for r in output} == set(fields)
    out = io.StringIO(); writer = csv.DictWriter(out, fieldnames=list(output[0]), lineterminator='\n')
    writer.writeheader(); writer.writerows(output)
    if write:
        (root / 'values.csv').write_text(out.getvalue())
    else:
        assert (root / 'values.csv').read_text() == out.getvalue(), '新增字段证据改变'
    print(f'通过：{len(fields)} 个 FN，{len(output)} 个原文金额及源位；PDF 重提取由上游执行。')


if __name__ == '__main__':
    # 父验证先重提取两公司 PDF，再调用本模块；没有仅核对 CSV 的伪离线验收。
    subprocess.run([sys.executable, str(ROOT.parent / 'valuation-chain-2026/verify.py')], check=True)
