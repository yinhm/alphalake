"""安克专用离线验收；重提取 PDF、核对源位和恒等式，再复算财务输入表。"""
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
    assert len(rows) == 597 and len({r['id'] for r in rows}) == len(rows)
    values = {}
    cf_comparatives = {}
    for r in rows:
        text = texts[r['pdf_id']]
        start = text.index(r['section'])
        section = text[start:text.index(r['end_section'], start + len(r['section']))]
        matches = list(re.finditer('^' + re.escape(r['label']) + (r'[\s\S]*' if r['end_section'] == '长期借款' else r'[^\n]*'), section, re.M))
        match = matches[int(r['occurrence'])]
        page = int(re.findall(r'=== 页 (\d+) ===', text[:start+match.start()])[-1])
        tokens = re.findall(r'(?<!\S)(?:-?[\d,]+\.\d{2}|-)(?!\S)', match[0])
        token = tokens[int(r['column'])]
        value = D('0' if token == '-' else token.replace(',', ''))
        assert token == r['printed'] and page == int(r['pdf_page']) and value == D(r['value']), r
        values[r['id']] = value
        if r['period'] == '2026-06-30' and r['kind'] == 'ytd' and r['key'].startswith('cf_'):
            token = tokens[1]
            cf_comparatives[r['key']] = D('0' if token == '-' else token.replace(',', ''))
    assert len(cf_comparatives) == 29
    for key, value in cf_comparatives.items():
        assert values['2025-06-30/'+key] == value, key
    def v(period, key):
        return values[period + '/' + key]  # 缺项直接失败，绝不默认零。
    def total(period, keys):
        return sum((v(period, k) for k in keys.split()), D(0))

    # 报表与附注的代数一致性，不等同两条独立证据链。
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
    assert len(source_rows) == len(mapped) == 84
    for r in source_rows:
        evidence = mapped[(r['period'], r['field'])]
        assert r['pdf_value'] == evidence['value'] and r['pdf_page'] == evidence['pdf_page']
        meta = reports[evidence['pdf_id']]
        assert r['pdf_url'] == meta['url'] and r['pdf_sha256'] == meta['sha256']
        multiplier = 10000 if r['field'] in ('FN439', 'FN581') else 1
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
        # 同一组原文数据上的利润总额路径代数一致性校验。
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
    # 现金流量主表与补充资料交叉核对；不将会计 OCF 当作 FCFF。
    cf_keys = 'net_income asset_impairment credit_impairment property_depreciation fixed_depreciation rou_depreciation intangible_amortization deferred_amortization disposal_loss retirement_loss fair_value finance investment deferred_tax_assets deferred_tax_liabilities inventory receivables payables share_payment'.split()
    da_keys = 'property_depreciation fixed_depreciation rou_depreciation intangible_amortization deferred_amortization'.split()
    reinvestment = {}
    for p in PERIODS:
        signed = evaluate('cashflow', p, [('cf_' + k, 1) for k in cf_keys])
        assert signed == v(p, 'cf_operating_cash_flow') == v(p, 'cf_ocf_statement')
        assert signed == v(p, 'cf_cash_in') - v(p, 'cf_cash_out')
        assert v(p, 'cf_investing_out') == total(p, 'cf_capex cf_investments_paid cf_other_investing_paid')
        assert v(p, 'cf_net_income') == v(p, 'net_income')
        assert v(p, 'cf_finance') == v(p, 'interest_expense')
        assert v(p, 'cf_investment') == -v(p, 'investment_income')
        assert v(p, 'cf_fair_value') == -v(p, 'fair_value_income')
        emit('cashflow', p, 'operating_cash_flow', signed, 'sum(signed reconciliation rows)=statement cash_in-cash_out', 'statement_reconciled_not_fcff')
        opening = '2025-12-31' if p == '2026-06-30' else '2024-12-31'
        for date in (p, opening):
            assert v(date, 'cf_gross_inventory') - v(date, 'cf_inventory_allowance') == v(date, 'inventory')
        assert -v(p, 'cf_inventory') == v(p, 'cf_gross_inventory') - v(opening, 'cf_gross_inventory')
        da = total(p, ' '.join('cf_' + k for k in da_keys))
        cf_wc = -total(p, 'cf_inventory cf_receivables cf_payables')
        balance_wc = wc[p] - wc[opening]
        allowance = v(p, 'cf_inventory_allowance') - v(opening, 'cf_inventory_allowance')
        data = {'cash_capex': (v(p, 'cf_capex'), p+'/cf_capex', 'reported_cash_not_total_reinvestment'),
                'reported_da': (da, '+'.join(p+'/cf_'+k for k in da_keys), 'includes_rou_and_investment_property'),
                'da_excluding_rou': (da-v(p, 'cf_rou_depreciation'), 'reported_da-rou_depreciation', 'component_not_fcff'),
                'lease_payment': (v(p, 'cf_lease_payment'), p+'/cf_lease_payment', 'financing_payment_not_capex'),
                'cf_wc_cash_use': (cf_wc, '-(cf_inventory+cf_receivables+cf_payables)', 'cashflow_statement_scope'),
                'broad_wc_balance_increase': (balance_wc, p+'/noncash_nondebt_wc_broad-'+opening+'/noncash_nondebt_wc_broad', 'balance_sheet_policy_scope'),
                'cf_minus_balance_wc': (cf_wc-balance_wc, 'cf_wc_cash_use-broad_wc_balance_increase', 'scope_difference_not_zero'),
                'inventory_allowance_change': (allowance, p+'/cf_inventory_allowance-'+opening+'/cf_inventory_allowance', 'identified_inventory_net_gross_difference'),
                'remaining_wc_scope_difference': (cf_wc-balance_wc-allowance, 'cf_minus_balance_wc-inventory_allowance_change', 'unattributed_not_forced_to_zero')}
        reinvestment[p] = data
        for k, (amount, formula, status) in data.items():
            emit('reinvestment', p, k, amount, formula, status)
    for key in ['cf_'+k for k in cf_keys] + ['operating_cash_flow']:
        amounts = {r['period']: D(r['value']) for r in results['cashflow'] if r['item'] == key}
        emit('cashflow', 'TTM-2026-06-30', key, amounts[PERIODS[1]]+amounts[PERIODS[2]]-amounts[PERIODS[0]], f'2025-12-31/{key}+2026-06-30/{key}-2025-06-30/{key}', 'statement_reconciled_not_fcff')
    for key in reinvestment[PERIODS[0]]:
        amount = reinvestment[PERIODS[1]][key][0]+reinvestment[PERIODS[2]][key][0]-reinvestment[PERIODS[0]][key][0]
        emit('reinvestment', 'TTM-2026-06-30', key, amount, f'2025-12-31/{key}+2026-06-30/{key}-2025-06-30/{key}', reinvestment[PERIODS[2]][key][2])
    for period in ('FY-2025', 'TTM-2026-06-30'):
        results['reinvestment'].append(dict(period=period, item='valuation_fcff', value='', unit='CNY', status='missing_valuation_adjustments', formula='unresolved WC classification/lease reinvestment and tax policy'))
    # 租赁资产、租赁负债和所得税的明细桥接，仍属于公司样本分析层。
    tax_keys = 'tax_base tax_subsidiary_rates tax_prior_periods tax_nontaxable tax_nondeductible tax_prior_losses tax_unrecognized_losses tax_rd_deduction'.split()
    for p in PERIODS:
        d = lambda key: v(p, 'detail_'+key)
        assert d('rou_gross_open')+d('rou_additions')-d('rou_disposals')+d('rou_gross_fx') == d('rou_gross_close')
        assert d('rou_dep_open')+d('rou_depreciation')-d('rou_dep_disposals')+d('rou_dep_fx') == d('rou_dep_close')
        assert d('rou_gross_open')-d('rou_dep_open') == d('rou_net_open')
        assert d('rou_gross_close')-d('rou_dep_close') == d('rou_net_close')
        assert d('rou_depreciation') == v(p, 'cf_rou_depreciation')
        assert d('lease_liability_open') == d('lease_open')
        assert d('lease_liability_close') == v(p, 'all_leases')
        assert d('lease_liability_cash_out') == v(p, 'cf_lease_payment')
        assert d('lease_liability_open')+d('lease_liability_cash_in')+d('lease_liability_noncash_in')-d('lease_liability_cash_out')-d('lease_liability_noncash_out') == d('lease_liability_close')
        assert d('rou_additions')+v(p, 'lease_interest') == d('lease_liability_noncash_in')
        assert d('current_tax')+d('deferred_tax') == v(p, 'tax_expense')
        assert sum((d(k) for k in tax_keys), D(0)) == v(p, 'tax_expense')
        emit('tax-bridge', p, 'disclosed_base_minus_15pct_reference', d('tax_base')-(v(p,'pbt')*D('.15')).quantize(D('.01'), rounding=ROUND_HALF_UP), 'disclosed tax_base-round(profit_before_tax*15%,2)', 'reported_difference_not_overwritten')
        for k in tax_keys+['current_tax','deferred_tax']:
            emit('tax-bridge', p, k, d(k), p+'/detail_'+k, 'disclosed_tax_not_cash_paid')
        emit('tax-bridge', p, 'total_tax_expense', v(p,'tax_expense'), 'current_tax+deferred_tax=sum(tax reconciliation)', 'not_operating_tax_allocation')
        for k in ['rou_additions','rou_disposals','rou_depreciation','rou_gross_fx','rou_dep_disposals','rou_dep_fx','lease_liability_cash_in','lease_liability_noncash_in','lease_liability_cash_out','lease_liability_noncash_out']:
            emit('lease-reinvestment', p, k, d(k), p+'/detail_'+k, 'disclosed_movement')
        emit('lease-reinvestment', p, 'lease_interest', v(p,'lease_interest'), p+'/lease_interest', 'accrual_not_cash_interest')
        emit('lease-reinvestment', p, 'cash_capex_plus_rou_additions', v(p,'cf_capex')+d('rou_additions'), p+'/cf_capex+'+p+'/detail_rou_additions', 'cash_and_noncash_input_not_final_fcff')
        opening = '2025-12-31' if p=='2026-06-30' else '2024-12-31'
        def receivable_balance(date):
            fixed = D(0) if date=='2026-06-30' else v(date,'fixed_notes')
            return total(date,'current_assets trading_forward_asset')-total(date,'cash trading_assets deposits inventory')-fixed
        def payable_balance(date):
            return v(date,'current_liabilities')-total(date,'short_debt current_debt')
        rec_change = receivable_balance(p)-receivable_balance(opening)
        pay_change = payable_balance(p)-payable_balance(opening)
        rec_gap = -v(p,'cf_receivables')-rec_change
        pay_gap = pay_change-v(p,'cf_payables')
        assert rec_gap+pay_gap == reinvestment[p]['remaining_wc_scope_difference'][0]
        components = {'receivable_balance_increase': (rec_change, 'noninventory broad current assets: close-open', 'balance_policy_scope'),
                      'payable_balance_increase': (pay_change, 'nondebt current liabilities: close-open', 'balance_policy_scope'),
                      'receivable_cash_use': (-v(p,'cf_receivables'), '-'+p+'/cf_receivables', 'cashflow_statement_scope'),
                      'payable_cash_source': (v(p,'cf_payables'), p+'/cf_payables', 'cashflow_statement_scope'),
                      'receivable_scope_gap': (rec_gap, 'receivable_cash_use-receivable_balance_increase', 'unattributed_not_zero'),
                      'payable_scope_gap': (pay_gap, 'payable_balance_increase-payable_cash_source', 'unattributed_not_zero'),
                      'income_tax_payable_change': (v(p,'income_tax_payable')-v(opening,'income_tax_payable'), p+'/income_tax_payable-'+opening+'/income_tax_payable', 'candidate_not_attributed_to_gap')}
        for key,(amount,formula,status) in components.items():
            emit('wc-components', p, key, amount, formula, status)
        # 应收侧余额全量分解；准备余额变动不冒充现金流差额的经济归因。
        balances = {}
        for date in (opening, p):
            for prefix, net_key in [('ar', 'receivables'), ('other', 'rec_other_net')]:
                assert v(date,'rec_'+prefix+'_gross')-v(date,'rec_'+prefix+'_allowance') == v(date,net_key)
            assert v(date,'rec_ar_net') == v(date,'receivables')
            assert total(date,'rec_other_net rec_dividend') == v(date,'other_receivables')
            assert v(date,'rec_hedge_asset') == v(date,'derivative_assets')
            fixed = D(0) if date=='2026-06-30' else v(date,'fixed_notes')
            balances[date] = {k:v(date,k) for k in ['receivables','prepayments','other_receivables','trading_forward_asset','derivative_assets']}
            balances[date]['other_current_assets_ex_deposits_notes'] = v(date,'other_current_assets')-v(date,'deposits')-fixed
            assert sum(balances[date].values(), D(0)) == receivable_balance(date)
        for key in balances[p]:
            emit('receivables-bridge', p, key+'_change', balances[p][key]-balances[opening][key], f'{p}/{key}-{opening}/{key}', 'balance_component_not_cashflow_attribution')
        provision = D(0)
        allowance_change = D(0)
        for prefix in ['ar','other']:
            r = lambda key: v(p,'rec_'+prefix+'_'+key)
            assert r('open') == v(opening,'rec_'+prefix+'_allowance')
            assert r('close') == r('allowance')
            assert r('open')+r('charge')-r('recovery')-r('writeoff')+r('fx') == r('close')
            provision += r('charge')-r('recovery')
            allowance_change += r('close')-r('open')
            for key in ['charge','recovery','writeoff','fx']:
                emit('receivables-bridge', p, prefix+'_'+key, r(key), p+'/rec_'+prefix+'_'+key, 'disclosed_allowance_movement')
        assert provision == v(p,'cf_credit_impairment') == -v(p,'credit_impairment')
        forward_change = total(p,'trading_forward_asset derivative_assets')-total(opening,'trading_forward_asset derivative_assets')
        ex_forward_gap = rec_gap+forward_change
        emit('receivables-bridge', p, 'receivable_allowance_change', allowance_change, 'sum(ar and other allowance close-open)', 'verified_net_gross_difference_not_cashflow_attribution')
        emit('receivables-bridge', p, 'net_credit_impairment', provision, 'ar_charge-ar_recovery+other_charge-other_recovery=cf_credit_impairment', 'statement_reconciled')
        emit('receivables-bridge', p, 'forward_asset_change', forward_change, 'trading_forward_asset_change+derivative_assets_change', 'balance_component_not_cashflow_attribution')
        emit('receivables-bridge', p, 'gap_excluding_forward_assets', ex_forward_gap, 'receivable_scope_gap+forward_asset_change', 'sensitivity_not_attributed_or_base_policy')
        emit('receivables-bridge', p, 'gap_excluding_forward_assets_gross_receivables', ex_forward_gap-allowance_change, 'gap_excluding_forward_assets-receivable_allowance_change', 'sensitivity_not_attributed_or_base_policy')
        # 套期储备的列报关系可闭合，不据此反推结算现金。
        h = lambda key: v(p,'hedge_oci_'+key)
        assert h('pretax')-h('reclassified')-h('tax') == h('parent')+h('minority')
        assert h('open')+h('parent') == h('close')
        assert h('parent') == v(p,'hedge_statement_oci')
        for date in (opening,p):
            assert v(date,'hedge_trading_liability') == v(date,'trading_liabilities')
            assert v(date,'hedge_derivative_liability') == v(date,'derivative_liabilities')
        for key in ['pretax','reclassified','tax','parent','minority']:
            emit('hedge-bridge', p, 'oci_'+key, h(key), p+'/hedge_oci_'+key, 'reported_oci_not_settlement_cash')
        liability_change = total(p,'trading_liabilities derivative_liabilities')-total(opening,'trading_liabilities derivative_liabilities')
        net_forward_change = forward_change-liability_change
        derivative_change = v(p,'derivative_assets')-v(p,'derivative_liabilities')-v(opening,'derivative_assets')+v(opening,'derivative_liabilities')
        emit('hedge-bridge', p, 'forward_asset_change', forward_change, 'change(trading_forward_asset+derivative_assets)', 'balance_change_not_settlement_cash')
        emit('hedge-bridge', p, 'forward_liability_change', liability_change, 'change(trading_liabilities+derivative_liabilities)', 'balance_change_not_settlement_cash')
        emit('hedge-bridge', p, 'net_forward_asset_change', net_forward_change, 'forward_asset_change-forward_liability_change', 'balance_change_not_settlement_cash')
        emit('hedge-bridge', p, 'oci_net_pretax_minus_derivative_change', h('pretax')-h('reclassified')-derivative_change, 'oci_pretax-oci_reclassified-change(derivative_assets-derivative_liabilities)', 'reported_scope_difference_not_overwritten')
        emit('hedge-bridge', p, 'forward_disposal_profit', v(p,'forward_realized'), p+'/forward_realized', 'investment_income_not_settlement_cash')
        emit('hedge-bridge', p, 'forward_fair_value_profit', total(p,'fv_forward_asset fv_forward_liability'), 'fv_forward_asset+fv_forward_liability', 'profit_not_settlement_cash')
        gap = reinvestment[p]['remaining_wc_scope_difference'][0]+net_forward_change
        assert gap == ex_forward_gap+pay_gap-liability_change
        emit('hedge-bridge', p, 'wc_gap_excluding_forward_assets_and_liabilities', gap, 'remaining_wc_scope_difference+net_forward_asset_change', 'sensitivity_not_attributed_or_base_policy')
        emit('hedge-bridge', p, 'wc_gap_excluding_forwards_gross_receivables', gap-allowance_change, 'wc_gap_excluding_forward_assets_and_liabilities-receivable_allowance_change', 'sensitivity_not_attributed_or_base_policy')
    assert v(PERIODS[0],'hedge_oci_open') == v(PERIODS[1],'hedge_oci_open')
    assert v(PERIODS[2],'hedge_oci_open') == v(PERIODS[1],'hedge_oci_close')
    assert v(PERIODS[2],'hedge_comparative_oci') == v(PERIODS[0],'hedge_statement_oci')
    for table in ['tax-bridge','lease-reinvestment','wc-components','receivables-bridge','hedge-bridge']:
        keys = list(dict.fromkeys(r['item'] for r in results[table]))
        for key in keys:
            group = {r['period']:r for r in results[table] if r['item']==key}
            amount = D(group[PERIODS[1]]['value'])+D(group[PERIODS[2]]['value'])-D(group[PERIODS[0]]['value'])
            emit(table, 'TTM-2026-06-30', key, amount, f'2025-12-31/{key}+2026-06-30/{key}-2025-06-30/{key}', group[PERIODS[2]]['status'])
    for period in PERIODS+['TTM-2026-06-30']:
        results['hedge-bridge'].append(dict(period=period, item='derivative_settlement_cash', value='', unit='CNY', status='missing_separate_cash_disclosure', formula='no complete settlement cash and cashflow classification bridge in the three archived reports'))
    for name, data in results.items():
        for row in data:
            row['as_of'] = AS_OF
        path = ROOT / (name + '.csv')
        expected = csv_text(data)
        if write:
            path.write_text(expected)
        else:
            assert path.read_text() == expected, f'{path.name}: run --write only after reviewing input/policy changes'
    print(f'通过：{len(rows)} 个 PDF 金额、84 个源字段位比较、报表/债务恒等式及十张 Decimal 输入表。')
    for name in results:
        for r in results[name]:
            if r['item'] in ('ebit_financing_and_investment_adjusted', 'interest_bearing_debt_book_value', 'noncash_nondebt_wc_broad', 'wc_after_identified_exclusions', 'change_in_broad_wc'):
                print(name, r['period'], r['item'], r['value'], r['status'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='验收通过后重建分析 CSV；默认只核对')
    main(parser.parse_args().write)
