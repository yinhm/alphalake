"""从规范三表推导资产账面小计；不将混合资产小计认证为估值总额。"""
import argparse
from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path


LONG_TERM = ('long_term_equity_investments', 'debt_investments',
             'other_debt_investments', 'other_equity_instrument_investments',
             'other_noncurrent_financial_assets')
CONTEXT = ('other_current_assets', 'noncurrent_assets_due_within_one_year',
           'derivative_financial_assets', 'other_noncurrent_assets',
           'available_for_sale_financial_assets', 'held_to_maturity_investments',
           'closing_cash', 'closing_cash_equivalents')
FORMULAS = {
    'monetary_funds_plus_trading_assets': (('monetary_funds', 1), ('trading_financial_assets', 1)),
    'cash_equivalents_plus_trading_assets': (('cash_and_cash_equivalents', 1), ('trading_financial_assets', 1)),
    'monetary_funds_less_cash_equivalents': (('monetary_funds', 1), ('cash_and_cash_equivalents', -1)),
    'long_term_investment_components': tuple((f, 1) for f in LONG_TERM),
}


def derive(snapshot):
    if (snapshot['contract_version'], snapshot['identity_status'], snapshot['statement_scope']) != (
            'alphalake-financial-statements-v1', 'resolved', 'provider_default'):
        raise ValueError('resolved standard statement snapshot required')
    period = date.fromisoformat(snapshot['report_period'])
    cutoff = datetime.fromisoformat(snapshot['information_as_of'].replace('Z', '+00:00'))
    if cutoff.utcoffset() is None or cutoff.date() < period:
        raise ValueError('invalid information cutoff')
    wanted = set(CONTEXT) | {f for terms in FORMULAS.values() for f, _ in terms}
    rows, values = {}, {}
    for table in snapshot['statements'].values():
        for row in table:
            field = row['field']
            if field not in wanted:
                continue
            if field in rows:
                raise ValueError('duplicate standard field: '+field)
            rows[field] = row
            if row['status'] != 'available':
                continue
            if (row['unit'], row['period_basis'], row['balance_date']) != ('CNY', 'instant', period.isoformat()):
                raise ValueError('incompatible unit/period: '+field)
            amount = Decimal(row['value'])
            if not amount.is_finite() or amount < 0:
                raise ValueError('invalid asset amount: '+field)
            values[field] = amount
    calculations = {}
    for name, terms in FORMULAS.items():
        present = [(f, c) for f, c in terms if f in values]
        missing = {f: rows.get(f, {}).get('status', 'field_not_supplied') for f, _ in terms if f not in values}
        subtotal = sum((values[f] * c for f, c in present), Decimal(0)) if present else None
        # 缺项仅参与缺口列表；已知小计不写成完整公式值，更不把源零补成事实。
        calculations[name] = dict(
            value_cny=str(subtotal) if not missing else None,
            known_subtotal_cny=str(subtotal) if subtotal is not None else None,
            status='arithmetic_complete_not_valuation_approved' if not missing else 'incomplete_components',
            terms=[dict(field=f, coefficient=c) for f, c in terms], missing=missing)
    return dict(contract_version='tdx-asset-derivation-v1', code=snapshot['code'],
        report_period=period.isoformat(), information_as_of=snapshot['information_as_of'],
        calculations=calculations,
        context={f: dict(status=rows.get(f, {}).get('status', 'field_not_supplied'),
                         value_cny=str(values[f]) if f in values else None) for f in CONTEXT},
        source_evidence={f: rows[f] for f in sorted(rows)},
        valuation_approved=False,
        unresolved=['货币资金与现金等价物差额不等于受限资金',
                    '交易性金融资产可能混有经营套期，且现金等价物可能与投资重叠',
                    '其他流动资产及到期非流动资产可能包含未单列存款或投资',
                    '长期投资缺分量及旧新准则分类不能默认归零或重复相加',
                    '账面资产加回须与经营收益剔除及资本口径一致'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path, help='financial-statements --include-evidence JSON')
    args = parser.parse_args()
    print(json.dumps(derive(json.loads(args.snapshot.read_text())), ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
