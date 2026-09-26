"""从标准三表列出金融兼营的配套分量；不批准估值或伪造去合并报表。"""
import argparse
from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path


POOL = dict.fromkeys(('monetary_funds', 'funds_lent',
    'financial_assets_purchased_under_resale_agreements',
    'noncurrent_assets_due_within_one_year', 'loans_and_advances_noncurrent',
    'debt_investments', 'other_debt_investments'), 1)
NET_INTEREST = {'financial_business_interest_income': 1,
    'financial_business_interest_expense': -1, 'financial_business_fee_expense': -1}
EARNINGS = {'operating_profit_cumulative': 1,
    **{f: -c for f, c in NET_INTEREST.items()}, 'interest_expense': 1,
    'interest_income': -1, 'investment_income': -1, 'fair_value_change_income': -1,
    'credit_impairment_income': -1, 'asset_disposal_income': -1}
FORMULAS = {
    'financial_asset_pool': ('instant', POOL),
    'financial_pool_less_deposits': ('instant', {**POOL, 'deposits_and_interbank_placements': -1}),
    'equity_investment_book_components': ('instant', {'long_term_equity_investments': 1, 'other_noncurrent_financial_assets': 1}),
    'cash_equivalents_less_monetary_funds': ('instant', {'cash_and_cash_equivalents': 1, 'monetary_funds': -1}),
    'identified_financial_net_interest': ('ytd', NET_INTEREST),
    'earnings_after_identified_financial_items': ('ytd', EARNINGS),
}


def derive(snapshot):
    if (snapshot['contract_version'], snapshot['identity_status'], snapshot['statement_scope']) != (
            'alphalake-financial-statements-v1', 'resolved', 'provider_default'):
        raise ValueError('resolved standard statements required')
    period = date.fromisoformat(snapshot['report_period'])
    cutoff = datetime.fromisoformat(snapshot['information_as_of'].replace('Z', '+00:00'))
    if cutoff.utcoffset() is None or cutoff.date() < period:
        raise ValueError('invalid cutoff')
    rows = {}
    for table in snapshot['statements'].values():
        for row in table:
            if row['field'] in rows:
                raise ValueError('duplicate field')
            rows[row['field']] = row
    wanted = {f for _, terms in FORMULAS.values() for f in terms}
    identities = {r['instrument_id'] for f, r in rows.items() if f in wanted and r['status'] == 'available'}
    if len(identities) > 1:
        raise ValueError('mixed security identities')
    calculations = {}
    for name, (basis, terms) in FORMULAS.items():
        total, missing = Decimal(0), {}
        for field, coefficient in terms.items():
            row = rows.get(field, {})
            if row.get('status') != 'available':
                missing[field] = row.get('status', 'field_not_supplied')
                continue
            if (row['unit'], row['period_basis']) != ('CNY', basis):
                raise ValueError('invalid unit or period: '+field)
            published = datetime.fromisoformat(row['announcement_time'])
            if published.utcoffset() is None or published > cutoff:
                raise ValueError('unavailable announcement: '+field)
            if basis == 'instant' and row['balance_date'] != period.isoformat():
                raise ValueError('invalid balance date: '+field)
            value = Decimal(row['value'])
            if not value.is_finite() or (basis == 'instant' and value < 0):
                raise ValueError('invalid amount: '+field)
            total += value * coefficient
        calculations[name] = dict(value_cny=None if missing else str(total),
            status='missing_components' if missing else 'diagnostic_not_valuation_approved',
            period_basis=basis, formula=terms, missing=missing)
    return dict(code=snapshot['code'], report_period=snapshot['report_period'],
        information_as_of=snapshot['information_as_of'], calculations=calculations,
        source_evidence={f: rows[f] for f in sorted(wanted & rows.keys())},
        valuation_approved=False, unresolved=[
            '到期非流动资产是混合总额；须验证金融组成，不能跨公司默认全部为投资',
            '金融资产池不叠加现金流量表现金等价物，亦不等于可分配超额现金',
            '金融净收支不等于财务公司利润；费用、税项、抵销与股东归属尚未分拆',
            '信用减值及处置损益整体剔除仅是诊断政策，不是纯主营经营收益',
            '尚未扣法定准备金、经营现金和其余权利请求，不可直接填入原模型现金列'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    args = parser.parse_args()
    print(json.dumps(derive(json.loads(args.snapshot.read_text())), ensure_ascii=False, indent=2, allow_nan=False))
