"""复用已重新提取的 PDF 台账，锁定新增 FN 的字段/期间/原文和源位证据。"""
import csv
import hashlib
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


def verify(write=False):
    output = []
    aliases = {'FY2025': '2025-12-31', 'H12025': '2025-06-30', 'H12026': '2026-06-30'}
    for idx, (company, code) in enumerate([('anker', '300866'), ('moutai', '600519')]):
        directory = ROOT.parent / (company + '-valuation-2026')
        with (directory / 'reported.csv').open() as f:
            ledger = list(csv.DictReader(f))
        for field, keys in FIELDS.items():
            for row in ledger:
                period, key = row['id'].split('/', 1)
                period = aliases.get(period, period)
                if key != keys[idx] or period not in aliases.values():
                    continue
                pdf = directory / (row['pdf_id'] + '.pdf')
                with zipfile.ZipFile(ROOT.parent / 'valuation-chain-2026' / ('gpcw' + period.replace('-', '') + '.zip')) as archive:
                    data = archive.read(archive.namelist()[0])
                records = [20 + n * 11 for n in range(struct.unpack_from('<H', data, 6)[0])]
                pos, = [p for p in records if data[p:p+6].decode() == code]
                offset = struct.unpack_from('<I', data, pos+7)[0]
                bits = struct.unpack_from('<I', data, offset+4*(int(field[2:])-1))[0]
                assert struct.pack('<I', bits) == struct.pack('<f', float(row['value'])), (code, period, field)
                output.append(dict(code=code, field=field, period=period, item=key,
                    period_basis='instant' if int(field[2:]) < 80 else 'ytd', pdf_value=row['value'],
                    pdf_page=row['pdf_page'], pdf_path='../'+directory.name+'/'+pdf.name,
                    pdf_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest()))
    assert len(output) == 38 and {r['field'] for r in output} == set(FIELDS)
    out = io.StringIO(); writer = csv.DictWriter(out, fieldnames=list(output[0]), lineterminator='\n')
    writer.writeheader(); writer.writerows(output)
    if write:
        (ROOT / 'values.csv').write_text(out.getvalue())
    else:
        assert (ROOT / 'values.csv').read_text() == out.getvalue(), '新增字段证据改变'
    print('通过：7 个新增 FN，38 个两公司原文金额及源位；PDF 原文重提取由上游验证执行。')


if __name__ == '__main__':
    # 父验证先重提取两公司 PDF，再调用本模块；没有仅核对 CSV 的伪离线验收。
    subprocess.run([sys.executable, str(ROOT.parent / 'valuation-chain-2026/verify.py')], check=True)
