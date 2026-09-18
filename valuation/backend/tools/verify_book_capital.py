"""六家两年资本分量核验与现金扣减敏感性；保留比较列重述。"""
from decimal import Decimal
import hashlib
import json
import re
import struct
import subprocess
from tools import verify_cnty_debt as cnty, verify_five_debt as five
from tools import verify_debt_maturities as maturity
from tools.tdx_research_source import source_value as value, financial_value, source_components

DIRECTORY = maturity.DIRECTORY
FIELDS = ('FN72', 'FN8', 'FN25', 'FN133')


def verify():
    for module in (cnty, five):
        module.verify(*module.load_inputs())
    _, reports, records, _ = maturity.load_inputs()
    ledger = json.loads((DIRECTORY / 'book-capital.json').read_bytes())
    expected = [(c, y) for c in ('000035', *five.CODES) for y in (2021, 2022)]
    if [(r['code'], r['year']) for r in ledger['reports']] != expected:
        raise ValueError('sample/period set differs')
    results = []
    restatement = None
    clean = lambda s: re.sub(r'\s+', '', s)
    for report in ledger['reports']:
        code, year = report['code'], report['year']
        path, sha = reports[code, year]
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise ValueError('PDF hash differs')
        text = subprocess.check_output(['pdftotext', '-layout', str(path), '-'], text=True)
        pages = text.split('\f')
        if (code, year) == ('000546', 2022):
            note = pages[193]
            if '可比期间财务报表已重新表述' not in clean(note) or '本公司自2022年1月1日起执行解释15号' not in clean(note):
                raise ValueError('restatement context differs')
            restatement = {}
            for label, expected_amount in [('在建工程', '-10419762.41'), ('存货', '4737327.88'),
                    ('递延所得税资产', '852365.18'), ('营业收入', '109783856.28'),
                    ('营业成本', '115466290.81'), ('净利润', '-4830069.35'), ('所得税费用', '-852365.18')]:
                lines = [l for l in note.splitlines() if clean(l).startswith(label)]
                amounts = re.findall(r'-?[0-9,]+\.[0-9]{2}', lines[0]) if len(lines) == 1 else []
                if len(amounts) != 1 or amounts[0].replace(',', '') != expected_amount:
                    raise ValueError('restatement amount differs')
                restatement[label] = expected_amount
        source = [r for r in records if r['code'] == code and r['period'] == f'{year}-12-31']
        if len(source) != 1 or tuple(r['field'] for r in report['rows']) != FIELDS:
            raise ValueError('source identity/field set differs')
        source = source[0]
        refs = []
        for row in report['rows']:
            field = row['field']
            start, end = (('5、合并现金流量表', '6、母公司现金流量表') if field == 'FN133'
                          else ('1、合并资产负债表', '2、母公司资产负债表'))
            if (row['section'], row['end_section']) != (start, end):
                raise ValueError('statement scope differs')
            body = text.split(start, 1)[1].split(end, 1)[0]
            header = clean(body[:500])
            date = f'{year}年度' if field == 'FN133' else f'{year}年12月31日'
            if '单位：元' not in header or date not in header:
                raise ValueError('statement unit/period differs')
            lines = [l for l in body.splitlines() if clean(l).startswith(row['label'])]
            if len(lines) != 1 or clean(lines[0]) != clean(row['line']) or lines[0] not in pages[row['page']-1].splitlines():
                raise ValueError('PDF row differs')
            amounts = [x.replace(',', '') for x in five.MONEY.findall(lines[0])]
            if amounts != [row['current_cny'], row['comparative_cny']]:
                raise ValueError('PDF columns differ')
            amount = Decimal(amounts[0])
            if struct.unpack('<I', struct.pack('<f', float(amount)))[0] != source['bits'][field]:
                raise ValueError('source bits differ')
            refs.append(dict(field=field, page=row['page'], pdf_cny=amounts[0], comparative_cny=amounts[1],
                             source_cny=str(value(source, field)), source_bits=source['bits'][field]))
        debt = sum(financial_value(source, f)
                   for f in ('short_term_borrowings', 'long_term_borrowings', 'current_portion_noncurrent_liabilities', 'bonds_payable', 'lease_liabilities'))
        gross = financial_value(source, 'total_equity') + debt
        cash_cases = {field: str(gross - financial_value(source, field)) for field in ('monetary_funds', 'cash_and_cash_equivalents')}
        results.append(dict(code=code, period=source['period'], source_matches=refs,
            common_debt_subtotal_cny=str(debt), equity_plus_common_debt_cny=str(gross),
            arithmetic_capital_after_full_cash_deduction_cny=source_components(cash_cases),
            monetary_funds_less_cash_equivalents_cny=str(financial_value(source, 'monetary_funds') - financial_value(source, 'cash_and_cash_equivalents')),
            long_term_equity_investments_cny=str(financial_value(source, 'long_term_equity_investments')),
            full_operating_capital=None, economic_roic=None))
    comparisons = []
    for code in ('000035', *five.CODES):
        prior, current = [r for r in results if r['code'] == code]
        for a, b in zip(prior['source_matches'], current['source_matches']):
            comparisons.append(dict(code=code, field=a['field'], period='2021-12-31',
                original_cny=a['pdf_cny'], later_comparative_cny=b['comparative_cny'],
                later_less_original_cny=str(Decimal(b['comparative_cny']) - Decimal(a['pdf_cny']))))
    delta = Decimal(next(r['later_less_original_cny'] for r in comparisons if r['code'] == '000546' and r['field'] == 'FN72'))
    if (sum(Decimal(restatement[k]) for k in ('在建工程', '存货', '递延所得税资产')) != delta
            or Decimal(restatement['营业收入']) - Decimal(restatement['营业成本']) - Decimal(restatement['所得税费用']) != delta
            or Decimal(restatement['净利润']) != delta):
        raise ValueError('restatement bridge differs')
    return dict(results=results, comparative_checks=comparisons,
        jinyuan_2021_restatement=dict(report_year=2022, page=194, amounts_cny=restatement,
                                     treatment='retain_original_source_and_later_comparative_separately'),
        boundary='six_companies_two_FY; 2023-09-01_China_cutoff; later_acquired_not_strict_PIT; '
        'cash_deduction_scenarios_not_excess_cash_facts; cash_difference_not_classified_restricted_cash; '
        'long_term_investments_not_automatically_nonoperating; negative_capital_not_clamped; '
        'comparative_changes_not_source_override; no_growth_or_ROIC_estimate')


if __name__ == '__main__':
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
