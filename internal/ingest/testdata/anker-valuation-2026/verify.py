"""安克专用离线验收；重提取 PDF、核对源位和恒等式，再复算三张财务输入表。"""
import argparse
import csv
import hashlib
import io
import json
import re
import struct
import zipfile
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent
D = Decimal
PERIODS = ['2025-06-30', '2025-12-31', '2026-06-30']
AS_OF = '2026-09-06T00:00:00+00:00'


def csv_text(rows):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def main(write=False):
    reports = json.loads((ROOT / 'reports.json').read_text())
    texts = {}
    for id, meta in reports.items():
        raw = (ROOT / meta['file']).read_bytes()
        assert len(raw) == meta['size'] and hashlib.sha256(raw).hexdigest() == meta['sha256'], id
        if id != 'catalogue':
            reader = PdfReader(io.BytesIO(raw))
            texts[id] = '\n'.join(f'=== 页 {i+1} ===\n' + p.extract_text() for i, p in enumerate(reader.pages))
    catalogue = json.loads((ROOT / 'catalogue.json').read_text())
    announcements = {a['announcementId']: a for a in catalogue['announcements']}
    for id in texts:
        a = announcements[id]
        assert a['secCode'] == '300866' and reports[id]['url'] == 'https://static.cninfo.com.cn/' + a['adjunctUrl']
        china = timezone(timedelta(hours=8))
        day = datetime.fromtimestamp(a['announcementTime'] / 1000, china).date()
        available = datetime.combine(day + timedelta(days=1), datetime.min.time(), china)
        assert available <= datetime.fromisoformat(AS_OF), id
    with (ROOT / 'reported.csv').open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 314 and len({r['id'] for r in rows}) == len(rows)
    values = {}
    for r in rows:
        text = texts[r['pdf_id']]
        start = text.index(r['section'])
        section = text[start:text.index(r['end_section'], start + len(r['section']))]
        matches = list(re.finditer('^' + re.escape(r['label']) + r'[^\n]*', section, re.M))
        match = matches[int(r['occurrence'])]
        page = int(re.findall(r'=== 页 (\d+) ===', text[:start+match.start()])[-1])
        tokens = re.findall(r'(?<!\S)(?:-?[\d,]+\.\d{2}|-)(?!\S)', match[0])
        token = tokens[int(r['column'])]
        value = D('0' if token == '-' else token.replace(',', ''))
        assert token == r['printed'] and page == int(r['pdf_page']) and value == D(r['value']), r
        values[r['id']] = value
    def v(period, key):
        return values[period + '/' + key]  # 缺项直接失败，绝不默认零。
    def total(period, keys):
        return sum((v(period, k) for k in keys.split()), D(0))

    # 独立的报表和附注加总，而非只确认一个数字能在 PDF 中找到。
    for p in PERIODS:
        assert v(p, 'loan_customer_balance') == v(p, 'loan_receivable')
        assert D(0) <= v(p, 'loan_allowance') <= v(p, 'loan_receivable')
        assert v(p, 'assets') == total(p, 'liabilities equity')
        assert v(p, 'pbt') == total(p, 'operating_profit nonoperating_income') - v(p, 'nonoperating_expense')
        assert v(p, 'net_income') == v(p, 'pbt') - v(p, 'tax_expense')
        assert v(p, 'operating_profit') == v(p, 'revenue') - v(p, 'total_costs') + total(p, 'other_income investment_income fair_value_income credit_impairment asset_impairment disposal_income')
        assert v(p, 'interest_expense') == total(p, 'loan_interest lease_interest')
        assert v(p, 'finance_costs') == total(p, 'interest_expense fx_loss fees cash_discount') - v(p, 'interest_income')
        assert v(p, 'investment_income') == total(p, 'associate_income equity_disposal dividend_income forward_realized wealth_income trading_disposal')
        assert v(p, 'fair_value_income') == total(p, 'fv_wealth_current fv_equity_current fv_forward_asset fv_wealth_noncurrent fv_equity_noncurrent fv_forward_liability')
        assert v(p, 'current_debt') == total(p, 'current_loans current_bonds current_leases')
        for net, current, gross in [('long_debt', 'current_loans', 'all_long_debt'), ('bonds', 'current_bonds', 'all_bonds'), ('leases', 'current_leases', 'all_leases')]:
            assert total(p, net + ' ' + current) == v(p, gross)
        other = total(p, 'logistics_payable marketing_payable rd_payable admin_payable deposits_payable other_payables_residual')
        if p != '2025-06-30':
            other += total(p, 'repurchase_payable capex_payable ipo_payable')
        assert other == v(p, 'other_payables')

    records = {}
    for package in json.loads((ROOT / 'packages.json').read_text()):
        raw = (ROOT / package['file']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == package['sample_sha256']
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            data = z.read(package['file'].replace('.zip', '.dat'))
        assert struct.unpack_from('<H', data, 6)[0] == 1
        code, _, offset = struct.unpack_from('<6sBI', data, 20)
        assert code == b'300866'
        period = package['file'][4:12]
        assert struct.unpack_from('<I', data, 2)[0] == int(period)
        records[period] = data[offset:offset+struct.unpack_from('<I', data, 12)[0]]
    mapped = {(r['period'], r['provider_field']): r for r in rows if r['provider_field']}
    with (ROOT / 'values.csv').open() as f:
        source_rows = list(csv.DictReader(f))
    assert len(source_rows) == len(mapped) == 69
    for r in source_rows:
        evidence = mapped[(r['period'], r['field'])]
        assert r['pdf_value'] == evidence['value'] and r['pdf_page'] == evidence['pdf_page']
        meta = reports[evidence['pdf_id']]
        assert r['pdf_url'] == meta['url'] and r['pdf_sha256'] == meta['sha256']
        multiplier = 10000 if r['field'] == 'FN439' else 1
        encoded = (D(r['pdf_value']) / multiplier).quantize(D('.01'), rounding=ROUND_HALF_UP)
        assert int(r['multiplier']) == multiplier and D(r['encoded_value']) == encoded
        data = records[r['period'].replace('-', '')]
        offset = (int(r['field'][2:]) - 1) * 4
        assert data[offset:offset+4] == struct.pack('<f', float(encoded)), r

    # 系数即本样本的显式分析政策，不写入全市场标准事实。
    ebit_terms = [('operating_profit', 1), ('interest_expense', 1), ('interest_income', -1),
                  ('investment_income', -1), ('forward_realized', 1), ('fair_value_income', -1),
                  ('fv_forward_asset', 1), ('fv_forward_liability', 1), ('disposal_income', -1)]
    debt_terms = [('short_debt', 1), ('long_debt', 1), ('current_loans', 1), ('bonds', 1),
                  ('current_bonds', 1), ('leases', 1), ('current_leases', 1)]
    wc_terms = [('current_assets', 1), ('cash', -1), ('trading_assets', -1),
                ('trading_forward_asset', 1), ('deposits', -1), ('current_liabilities', -1),
                ('short_debt', 1), ('current_debt', 1)]
    results = {}
    def emit(table, period, key, amount, formula, status='policy_defined'):
        results.setdefault(table, []).append(dict(period=period, item=key, value=f'{amount:.2f}', unit='CNY', status=status, formula=formula))
    def evaluate(table, period, terms):
        amount = D(0)
        for key, coefficient in terms:
            value = v(period, key) * coefficient
            amount += value
            emit(table, period, key, value, f'{coefficient}*{period}/{key}')
        return amount
    ebit = {}
    for p in PERIODS:
        ebit[p] = evaluate('ebit', p, ebit_terms)
        emit('ebit', p, 'ebit_financing_and_investment_adjusted', ebit[p], 'sum(component rows)')
        # 从利润总额独立起算，应得同一结果。
        assert ebit[p] == v(p, 'pbt') - v(p, 'nonoperating_income') + v(p, 'nonoperating_expense') + sum((v(p, k)*c for k,c in ebit_terms[1:]), D(0))
        fx = v(p, 'fx_loss') - total(p, 'forward_realized fv_forward_asset fv_forward_liability')
        emit('ebit', p, 'identified_fx_neutral_sensitivity', ebit[p]+fx, 'adjusted_ebit+fx_loss-forward_realized-fv_forward_asset-fv_forward_liability', 'sensitivity_not_base')
        emit('ebit', p, 'excluding_other_income_sensitivity', ebit[p]-v(p, 'other_income'), 'adjusted_ebit-other_income', 'sensitivity_not_base')
        debt = evaluate('debt', p, debt_terms)
        assert debt == total(p, 'short_debt all_long_debt all_bonds all_leases')
        emit('debt', p, 'interest_bearing_debt_book_value', debt, 'sum(component rows)', 'book_value_not_market_value')
        emit('debt', p, 'notes_payable_if_financing', debt+v(p, 'notes_payable'), 'book_debt+notes_payable', 'sensitivity_not_base')
    # 年度流量与 TTM 按同一政策线性组合，每个分量都保留三个来源。
    for key in [k for k,_ in ebit_terms] + ['ebit_financing_and_investment_adjusted', 'identified_fx_neutral_sensitivity', 'excluding_other_income_sensitivity']:
        items = {r['period']: D(r['value']) for r in results['ebit'] if r['item'] == key}
        emit('ebit', 'TTM-2026-06-30', key, items[PERIODS[1]]+items[PERIODS[2]]-items[PERIODS[0]], f'2025-12-31/{key}+2026-06-30/{key}-2025-06-30/{key}', 'sensitivity_not_base' if 'sensitivity' in key else 'policy_defined')
    wc = {}
    for p in ['2024-12-31'] + PERIODS:
        terms = wc_terms + ([('fixed_notes', -1)] if p != '2026-06-30' else [])
        wc[p] = evaluate('working-capital', p, terms)
        fixed = v(p, 'fixed_notes') if p != '2026-06-30' else D(0)
        # 本期附注 8 无固息票据行；全表加总确认无未解释余额，不把遗漏字段默认为零。
        oca = total(p, 'deposits oca_refund oca_vat oca_returns oca_other') + fixed
        if p == '2026-06-30':
            oca += v(p, 'ipo_prepaid')
        assert oca == v(p, 'other_current_assets')
        bottom_up = total(p, 'receivables prepayments other_receivables inventory other_current_assets trading_forward_asset derivative_assets') - v(p, 'deposits') - fixed - total(p, 'notes_payable payables contract_liabilities payroll tax_payable other_payables other_current_liabilities trading_liabilities derivative_liabilities')
        assert wc[p] == bottom_up
        emit('working-capital', p, 'noncash_nondebt_wc_broad', wc[p], 'sum(component rows)', 'broad_policy_includes_mixed_items')
        if p in PERIODS:
            known = [('loan_receivable', -1), ('loan_allowance', 1), ('dividends_receivable', -1), ('income_tax_payable', 1)]
            if p != '2025-06-30':
                known += [('ipo_prepaid', -1), ('repurchase_payable', 1), ('capex_payable', 1), ('ipo_payable', 1)]
            adjustment = evaluate('working-capital', p, known)
            emit('working-capital', p, 'wc_after_identified_exclusions', wc[p]+adjustment, 'broad_wc+identified_exclusion_rows', 'provisional_residual_classification')
            emit('working-capital', p, 'unclassified_other_payables', v(p, 'other_payables_residual'), p+'/other_payables_residual', 'unclassified_not_zero')
    for name, end, start in [('FY-2025', '2025-12-31', '2024-12-31'), ('H1-2026', '2026-06-30', '2025-12-31'), ('TTM-2026-06-30', '2026-06-30', '2025-06-30')]:
        emit('working-capital', name, 'change_in_broad_wc', wc[end]-wc[start], f'{end}/noncash_nondebt_wc_broad-{start}/noncash_nondebt_wc_broad', 'balance_change_not_cashflow_reconciliation')
    # 历史混合项拆分不全，不输出貌似精确的经营再投资差额。
    for period, reason in [('FY-2025', '2024 opening loan allowance and mixed classifications missing'), ('TTM-2026-06-30', '2025H1 other_payables residual includes undisclosed classifications')]:
        results['working-capital'].append(dict(period=period, item='change_in_fully_classified_operating_wc', value='', unit='CNY', status='missing_historical_classification', formula=reason))
    for name, data in results.items():
        for row in data:
            row['as_of'] = AS_OF
        path = ROOT / (name + '.csv')
        expected = csv_text(data)
        if write:
            path.write_text(expected)
        else:
            assert path.read_text() == expected, f'{path.name}: run --write only after reviewing input/policy changes'
    print(f'通过：{len(rows)} 个 PDF 金额、69 个源字段位比较、报表/债务恒等式及三张 Decimal 输入表。')
    for name in results:
        for r in results[name]:
            if r['item'] in ('ebit_financing_and_investment_adjusted', 'interest_bearing_debt_book_value', 'noncash_nondebt_wc_broad', 'wc_after_identified_exclusions', 'change_in_broad_wc'):
                print(name, r['period'], r['item'], r['value'], r['status'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='验收通过后重建三张 CSV；默认只核对')
    main(parser.parse_args().write)
