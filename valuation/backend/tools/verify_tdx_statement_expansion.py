"""离线核对整批标准字段：合并三表当期列→原PDF→TDX位→完整目录。"""
import argparse
import csv
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re
import struct
import zipfile

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[3]
DEFAULT = ROOT / 'internal/ingest/testdata/statement-expansion-2026'


def compact(text):
    # 安克三份报表的附注号在标签和金额之间；先去注号再去空格，
    # 不能对无空格文本贪婪匹配数字，否则会吞掉金额首位。
    return re.sub(r'\s+', '', re.sub(r'七、\d+(?:\(\d+\))?', '', text))


def verify(directory=DEFAULT, evidence=None):
    data = evidence if evidence is not None else json.loads((directory / 'evidence.json').read_text())
    catalog = {r['name']: r for r in csv.DictReader((ROOT / 'internal/source/tdx/financial/catalog.csv').open()) if r['name']}
    scopes = {'statement-expansion-20260919': (64, 188, 4, '2025-01-01'), 'cashflow-reconciliation-20260919': (12, 36, 0, '2025-01-01'), 'official-statement-units-20260919': (3, 9, 0, '1900-01-01')}
    fields, expected_matched, expected_missing, valid_from = scopes[data['review_id']]
    positions = fields * 3
    if data['valid_from'] != valid_from or len(data['approved_fields']) != fields or len(set(data['approved_fields'])) != fields:
        raise ValueError('reviewed field scope changed')
    if len(data['reports']) != 3 or len(data['observations']) != positions:
        raise ValueError('report/observation denominator changed')
    seen = set(); matched = missing = 0
    for report in data['reports']:
        pdf = directory / report['pdf_path']; package = directory / report['package_path']
        for path, expected in [(pdf, report['pdf_sha256']), (package, report['package_sha256'])]:
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError('original evidence hash differs: ' + str(path))
        reader = PdfReader(pdf)
        pages = {n: compact(reader.pages[n-1].extract_text()) for table in report['tables'].values() for n in table['pages']}
        for category, table in report['tables'].items():
            start = pages[table['pages'][0]]
            expected_period = report['period'].replace('-', '年', 1).replace('-', '月')+'日' if category == 'balance_sheet' else report['period'][:4] + ('年半年度' if report['period'].endswith('06-30') else '年度')
            if table['section'] not in start or '单位：元' not in start or table['header'] not in start or expected_period not in start:
                raise ValueError('consolidated statement unit/period header differs')
            if any('母公司'+title in pages[n] for n in table['pages'] for title in ['资产负债表','利润表','现金流量表']):
                raise ValueError('parent-only statement mixed into consolidated scope')
        with zipfile.ZipFile(package) as z:
            raw = z.read(z.namelist()[0])
        record_positions = [20+n*11 for n in range(struct.unpack_from('<H', raw, 6)[0])]
        record, = [p for p in record_positions if raw[p:p+6].decode() == report['code']]
        offset = struct.unpack_from('<I', raw, record+7)[0]
        for row in [r for r in data['observations'] if r['period'] == report['period']]:
            field = row['field']; definition = catalog[field]
            key = (field, row['period'])
            if key in seen or field not in data['approved_fields']:
                raise ValueError('duplicate/out-of-scope observation')
            seen.add(key)
            if row['pdf_id'] != report['pdf_id'] or row['pdf_page'] not in report['tables'][row['category']]['pages']:
                raise ValueError('PDF identity/page scope differs')
            if (row['provider_field'], str(row['multiplier']), row['period_basis']) != ('FN'+definition['index'], definition['multiplier'], definition['period_basis']):
                raise ValueError('catalog mapping/scale/period differs')
            text = pages[row['pdf_page']]
            decimals = row.get('pdf_decimals', 2)
            number = r'(-?[\d,]+\.\d{' + str(decimals) + r'}|-)'
            pattern = re.escape(row['label']) + number * 2
            hits = re.findall(pattern, text)
            if hits != [tuple(row['printed'])]:
                raise ValueError('PDF current/comparative cells differ: '+field)
            bits = struct.unpack_from('<I', raw, offset+4*(int(definition['index'])-1))[0]
            if bits != row['source_bits']:
                raise ValueError('TDX source bits changed')
            if row['status'] == 'printed_missing':
                if row['printed'][0] != '-' or row['pdf_value'] is not None or bits != 0:
                    raise ValueError('missing amount was fabricated')
                missing += 1
                continue
            if row['printed'][0] == '-':
                raise ValueError('missing amount was promoted to a reported value')
            amount = Decimal(row['printed'][0].replace(',', ''))
            if row.get('unit', definition['unit']) != definition['unit']:
                raise ValueError('normalized unit differs')
            precision = Decimal(1).scaleb(-row.get('encoding_decimals', 2))
            encoded = (amount/Decimal(row['multiplier'])).quantize(precision, rounding=ROUND_HALF_UP)
            if row['status'] != 'matched' or amount != Decimal(row['pdf_value']) or bits == 0 or struct.pack('<f', float(encoded)) != struct.pack('<I', bits):
                raise ValueError('nonzero original amount does not match source encoding: '+field)
            matched += 1
    if len(seen) != positions or matched != expected_matched or missing != expected_missing:
        raise ValueError('verification coverage changed')
    return {'fields': fields, 'company_count': 1, 'reports': 3, 'positions': positions, 'matched_nonzero': matched, 'printed_missing_source_zero': missing, 'pdf_cells': positions * 2}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', nargs='?', type=Path, default=DEFAULT)
    args = parser.parse_args()
    print(json.dumps(verify(args.directory), ensure_ascii=False))


if __name__ == '__main__':
    main()
