"""高权重训练样本原文定位及收入源精度核验；不改历史评分。"""
from decimal import Decimal
import hashlib
import json
import math
import re
import struct
import subprocess

from tools import backtest_tdx_zero_calibration as joint
from tools import diagnose_tdx_training_exclusions as exclusions
from tools.backtest_tdx_history import quarter_periods, value
from datetime import date

DIRECTORY = exclusions.DIRECTORY / 'source-review'


def verify(manifest=None):
    if manifest is None:
        manifest = json.loads((DIRECTORY / 'manifest.json').read_bytes())
    lines = {}
    for report in manifest:
        path = DIRECTORY / report['file']
        raw = path.read_bytes()
        if len(raw) != report['size'] or hashlib.sha256(raw).hexdigest() != report['sha256']:
            raise ValueError('PDF bytes differ')
        pages = subprocess.check_output(['pdftotext', '-layout', str(path), '-'], text=True).split('\f')
        for entry in report['evidence']:
            if entry['line'] not in [s.strip() for s in pages[entry['page'] - 1].splitlines()]:
                raise ValueError('PDF evidence line differs')
            lines[report['file'], entry['page'], entry['line'].split()[0]] = entry['line']
    def amounts(file, page, prefix):
        return [Decimal(n.replace(',', '')) for n in re.findall(r'-?\d[\d,]*(?:\.\d+)?', lines[file, page, prefix])]
    _, _, source = joint.load_inputs(exclusions.DIRECTORY / 'protocol.json')
    quarters = {}; matches = []
    for file, page, code, year, unit in (
        ('600751-2021FY.pdf', 8, '600751', 2021, 1000),
        ('688670-2023FY.pdf', 9, '688670', 2023, 1)):
        printed = amounts(file, page, '营业收入')
        if len(printed) != 4:
            raise ValueError('quarter columns differ')
        quarters[code] = [n * unit for n in printed]
        for end, amount in zip(('03-31', '06-30', '09-30', '12-31'), quarters[code]):
            found = [r for r in source['records'] if r['code'] == code and r['period'] == f'{year}-{end}']
            if len(found) != 1:
                raise ValueError('quarter source identity differs')
            pdf_bits = struct.unpack('<I', struct.pack('<f', float(amount)))[0]
            matches.append(dict(code=code, period=found[0]['period'], pdf_cny=str(amount),
                source_cny=str(value(found[0], 'FN230')), pdf_float32_bits=pdf_bits,
                source_bits=found[0]['bits']['FN230'],
                status='matched' if pdf_bits == found[0]['bits']['FN230'] else 'differs_from_later_annual_quarter_table'))
    result = exclusions.load_result(); comparisons = []
    for code, origin, end, side, half in (
        ('600751', '2023-06-30', '2022-06-30', 'prior', amounts('600751-2023H1.pdf', 6, '营业收入')[1] * 1000),
        ('688670', '2024-06-30', '2024-06-30', 'realized', amounts('688670-2024H1.pdf', 7, '营业收入')[0])):
        row = next(x for x in result['training'][origin]['admitted'] if x['code'] == code)
        current = row['prior']['current'] if side == 'prior' else row['realized']
        pdf = sum(quarters[code][2:]) + half
        records = [r for r in source['records'] if r['code'] == code and r['period'] in quarter_periods(date.fromisoformat(end))]
        if len(records) != 4:
            raise ValueError('TTM quarter identity differs')
        source_amount = sum(value(r, 'FN230') for r in records)
        bound = sum(Decimal(2) ** (math.frexp(abs(float(value(r, 'FN230'))))[1] - 25) for r in records)
        if abs(source_amount - pdf) > bound or float(source_amount) / 1e6 != current['revenue']:
            raise ValueError('TTM revenue exceeds source precision')
        comparisons.append(dict(code=code, end=end, pdf_cny=str(pdf), source_cny=str(source_amount),
                                delta_cny=str(source_amount-pdf), float32_rounding_bound_cny=str(bound)))
    expense = amounts('688670-2024H1.pdf', 12, '费用化研发投入')[0]
    capitalized = amounts('688670-2024H1.pdf', 12, '资本化研发投入')[0]
    total = amounts('688670-2024H1.pdf', 12, '研发投入合计')[0]
    if expense + capitalized != total:
        raise ValueError('R&D components differ')
    return dict(quarter_source_matches=matches, ttm_revenue_comparisons=comparisons,
        jindike_2024H1_rd=dict(expensed_cny=str(expense), capitalized_cny=str(capitalized), total_cny=str(total)),
        boundary='four_reports_eight_quarter_comparisons_two_TTM_revenue_checks; '
        'not_complete_EBIT_reconciliation; later_comparative_not_original_H1_verification; '
        'accounting_RD_total_not_cash_RD_or_future_return; no_source_override')


if __name__ == '__main__':
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
