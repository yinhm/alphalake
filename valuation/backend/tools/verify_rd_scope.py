"""金迪克两期研发范围：费用、投入流量和开发支出存量分开，不生成预测。"""
from decimal import Decimal
import gzip
import hashlib
import json
import re
import struct
import subprocess

from tools.verify_tdx_rd_semantics import DIRECTORY
from tools.verify_training_earnings import statement
from tools.backtest_tdx_history import value


def load_inputs():
    ledger = json.loads((DIRECTORY/'rd-scope.json').read_bytes())
    raw = gzip.decompress((DIRECTORY/'rd-scope-source.json.gz').read_bytes())
    if hashlib.sha256(raw).hexdigest() != ledger['source_sha256']:
        raise ValueError('source hash differs')
    return ledger, json.loads(raw)


def verify(ledger, source):
    expected = [('688670', '2023-12-31'), ('688670', '2024-06-30')]
    if [(r['code'], r['period']) for r in ledger['reports']] != expected:
        raise ValueError('report identities differ')
    if sorted((r['code'], r['period']) for r in source['records']) != expected:
        raise ValueError('source identities differ')
    results = []
    for report in ledger['reports']:
        path = DIRECTORY/report['file']; raw = path.read_bytes()
        if len(raw) != report['size'] or hashlib.sha256(raw).hexdigest() != report['sha256']:
            raise ValueError('PDF bytes differ')
        text = subprocess.check_output(['pdftotext', '-layout', str(path), '-'], text=True)
        pages = text.split('\f')
        amounts = {}
        for key, entry in report['entries'].items():
            if entry['line'] not in [s.strip() for s in pages[entry['page']-1].splitlines()]:
                raise ValueError('PDF line differs')
            # These fixed tables have current/prior amounts; management tables add a percentage.
            count = 3 if key == 'rollforward' else 2
            printed = re.findall(r'-?\d[\d,]*\.\d{2}', entry['line'])[:count]
            if len(printed) != count or printed != entry['values']:
                raise ValueError('PDF amounts differ')
            amounts[key] = [Decimal(n.replace(',', '')) for n in printed]
        for entry in report['evidence']:
            if entry['line'] not in [s.strip() for s in pages[entry['page']-1].splitlines()]:
                raise ValueError('PDF context differs')
        a = amounts
        profit = statement(text, 1, report['period'], {'FN304': '研发费用'})
        if Decimal(profit['FN304']['current_cny']) != a['expensed'][0]:
            raise ValueError('income statement expense differs')
        balance = text.split('合并资产负债表', 1)[1].split('母公司资产负债表', 1)[0]
        if report['entries']['balance']['line'] not in [s.strip() for s in balance.splitlines()]:
            raise ValueError('not consolidated development balance')
        if a['expensed'] != a['note_expensed'] or a['capitalized'] != a['note_capitalized']:
            raise ValueError('management/note scope differs')
        if any(a['expensed'][i]+a['capitalized'][i] != a['total'][i] for i in (0, 1)):
            raise ValueError('R&D total differs')
        opening, addition, closing = a['rollforward']
        if (opening+addition != closing or addition != a['capitalized'][0]
                or a['balance'] != [closing, opening]):
            raise ValueError('development rollforward differs')
        row = next(r for r in source['records'] if r['period'] == report['period'])
        comparisons = {}
        for field, amount in [('FN304', a['expensed'][0]), ('FN34', closing)]:
            if row['bits'][field] != struct.unpack('<I', struct.pack('<f', float(amount)))[0]:
                raise ValueError(field+' bits differ')
            comparisons[field] = dict(pdf_cny=str(amount), source_cny=str(value(row, field)),
                                      source_bits=row['bits'][field])
        results.append(dict(period=report['period'], comparisons=comparisons,
            expensed_yoy=str(a['expensed'][0]/a['expensed'][1]-1),
            total_yoy=str(a['total'][0]/a['total'][1]-1),
            capitalized_cny=str(addition), total_cny=str(a['total'][0]),
            rd_depreciation_amortization_cny=str(a['da'][0]),
            boundary='reported_RD_not_cash_investment; FN34_stock_not_flow; '
                     'two_fixed_periods_not_market_coverage; no_growth_forecast_or_source_override'))
    return results


if __name__ == '__main__':
    print(json.dumps(verify(*load_inputs()), ensure_ascii=False, indent=2))
