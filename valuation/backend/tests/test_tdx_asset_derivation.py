from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path

import pytest

from tools.derive_tdx_assets import derive


def test_subtotals_do_not_zero_fill_or_approve_assets():
    def row(field, value, status='available'):
        return dict(field=field, value=value, status=status, unit='CNY',
                    period_basis='instant', balance_date='2026-06-30')
    snapshot = dict(contract_version='alphalake-financial-statements-v1', identity_status='resolved',
        statement_scope='provider_default', code='300866', report_period='2026-06-30',
        information_as_of='2026-09-26T00:00:00Z', statements={'balance_sheet': [
            row('monetary_funds', '100.25'), row('trading_financial_assets', '20.50'),
            row('cash_and_cash_equivalents', '80'), row('long_term_equity_investments', '5'),
            row('other_noncurrent_financial_assets', '7'), row('debt_investments', None, 'source_zero_ambiguous')]})
    original = deepcopy(snapshot)
    result = derive(snapshot)
    assert snapshot == original
    assert result['calculations']['monetary_funds_plus_trading_assets']['value_cny'] == '120.75'
    assert result['calculations']['monetary_funds_less_cash_equivalents']['value_cny'] == '20.25'
    long_term = result['calculations']['long_term_investment_components']
    assert long_term['known_subtotal_cny'] == '12' and long_term['value_cny'] is None
    assert long_term['missing']['debt_investments'] == 'source_zero_ambiguous'
    assert not result['valuation_approved']
    snapshot['statements']['balance_sheet'][0]['unit'] = 'USD'
    with pytest.raises(ValueError, match='unit/period'):
        derive(snapshot)
    snapshot = original
    snapshot['statements']['balance_sheet'] = []
    assert derive(snapshot)['calculations']['long_term_investment_components']['known_subtotal_cny'] is None


def test_existing_cninfo_disproves_whole_other_assets_and_trading_addback():
    """原文仅为分类反例，不给TDX派生器提供金额，也不新增公司特判。"""
    from pypdf import PdfReader
    root = Path(__file__).resolve().parents[3] / 'internal/ingest/testdata/anker-valuation-2026'
    meta = json.loads((root/'reports.json').read_text())['1225533054']
    pdf = root/meta['file']
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == meta['sha256']
    pages = PdfReader(pdf).pages
    deposits = next(r for r in csv.DictReader((root/'reported.csv').open())
                    if r['period'] == '2026-06-30' and r['key'] == 'deposits')
    text = pages[int(deposits['pdf_page'])-1].extract_text()
    assert deposits['label']+' '+deposits['printed'] in text
    assert '待抵扣进项税 554,294,833.09' in text
    assert '未到期远期外汇合约 1,890,858.07' in pages[98].extract_text()
    assert '远期外汇合约 249,301,303.93' in pages[98].extract_text()
