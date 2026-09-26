"""核对本地TDX三表与既有茅台原文；PDF只验证，不参与诊断计算。"""
import argparse
import csv
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re
import struct

from pypdf import PdfReader
from tools.derive_financial_scope import derive

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT/'internal/ingest/testdata/moutai-valuation-2026'
# 既有冻结证据台账的条目名称，不是新的标准字段词典。
ITEMS = dict(zip(
    'monetary_funds funds_lent financial_assets_purchased_under_resale_agreements noncurrent_assets_due_within_one_year loans_and_advances_noncurrent debt_investments other_debt_investments deposits_and_interbank_placements long_term_equity_investments other_noncurrent_financial_assets cash_and_cash_equivalents financial_business_interest_income financial_business_interest_expense financial_business_fee_expense operating_profit_cumulative interest_expense interest_income investment_income fair_value_change_income credit_impairment_income asset_disposal_income'.split(),
    'cash interbank_assets reverse_repo current_financial_maturity loans debt_investments other_debt_investments deposits_liability associates funds cash_equivalents financial_interest_income financial_interest_expense financial_fees operating_profit borrowing_interest treasury_interest investment_income fair_value_income credit_impairment disposal_income'.split()))


def verify(snapshot):
    assert snapshot['code'] == '600519'
    result = derive(snapshot)
    reports = json.loads((FIXTURE/'reports.json').read_text())
    entries = json.loads((FIXTURE/'evidence.json').read_text())
    cells = {c['id']: (e,c) for e in entries for c in e['cells']}
    period = snapshot['report_period']
    flow = {'2025-06-30':'H12025', '2025-12-31':'FY2025', '2026-06-30':'H12026'}[period]
    with (ROOT/'internal/source/tdx/financial/catalog.csv').open() as f:
        multipliers = {r['name']: Decimal(r['multiplier']) for r in csv.DictReader(f) if r['multiplier']}
    documents, checks, unchecked = {}, [], []
    for field, row in result['source_evidence'].items():
        if row['status'] != 'available':
            continue
        key = (period if row['period_basis']=='instant' else flow)+'/'+ITEMS[field]
        if key not in cells:
            unchecked.append(field)
            continue
        entry, cell = cells[key]
        doc = reports[entry['pdf_id']]
        if entry['pdf_id'] not in documents:
            path = FIXTURE/doc['file']
            assert hashlib.sha256(path.read_bytes()).hexdigest() == doc['sha256']
            documents[entry['pdf_id']] = PdfReader(path)
        page = documents[entry['pdf_id']].pages[entry['pdf_page']-1].extract_text()
        assert page.count(entry['quote']) == 1
        tokens = re.findall(r'(?<![\d,])-?[\d,]+\.\d{2}(?!\d)',entry['quote'])
        assert tokens[cell['token']] == cell['printed']
        original = Decimal(cell['printed'].replace(',','')) * Decimal(cell['multiplier'])
        amount, scale = Decimal(row['value']), multipliers[field]
        encoded = original/scale
        candidates = [encoded]
        if scale == 10000:
            candidates.append(encoded.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
        matches = [i for i, v in enumerate(candidates) if abs(amount-Decimal(str(struct.unpack('<f',struct.pack('<f',float(v)))[0]*float(scale)))) <= Decimal('.00000001')]
        assert matches, (field, amount, original)
        checks.append(dict(field=field, standard_value=str(amount), pdf_value=str(original),
            difference_cny=str(amount-original), encoding='direct_float32' if 0 in matches else 'rounded_wanyuan_float32',
            pdf_page=entry['pdf_page'], pdf_sha256=doc['sha256']))
    cash_composition = None
    if period == '2026-06-30':
        page = re.sub(r'\s+', '', documents['1225475868'].pages[81].extract_text())
        labels = ['库存现金', '可随时用于支付的银行存款', '可随时用于支付的其他货币资金',
                  '可用于支付的存放中央银行款项', '存放同业款项']
        parts = {}
        for label in labels:
            matches = re.findall(re.escape(label)+r'([\d,]+\.\d{2})', page)
            assert len(matches) == 1
            parts[label] = Decimal(matches[0].replace(',',''))
        total = sum(parts.values(), Decimal(0))
        assert total == Decimal(cells[period+'/cash_equivalents'][1]['value'])
        cash_composition = dict(parts={k:str(v) for k,v in parts.items()}, total_cny=str(total),
                                scope='PDF_validation_only_not_model_input')
    return dict(code=snapshot['code'], report_period=period, checks=checks, cash_composition=cash_composition,
                unchecked_available_fields=unchecked, scope='source_precision_agreement_not_deconsolidation')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(json.loads(args.snapshot.read_text())), ensure_ascii=False, indent=2))
