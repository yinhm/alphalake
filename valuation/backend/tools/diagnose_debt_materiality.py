"""比较已识别缺口与五字段小计；不输出完整债务或ROIC。"""
from decimal import Decimal
import json
from tools import verify_financing_payables as financing
from tools import verify_debt_maturities as maturity
from tools import verify_cnty_debt as cnty, verify_five_debt as five
from tools.backtest_tdx_history import value


def diagnose():
    # Reuse original-PDF and source-bit checks before computing any denominator.
    parents = {}
    for module in (cnty, five):
        ledger, source = module.load_inputs()
        parents[module] = (ledger, module.verify(ledger, source))
    payable = {(r['code'], r['period']): r for r in financing.verify()['results']}
    inputs = maturity.load_inputs()
    bases = maturity.verify(*inputs)['results']
    results = []
    for base in bases:
        code, period = base['code'], base['period']
        matches = [r for r in inputs[2] if (r['code'], r['period']) == (code, period)]
        if len(matches) != 1:
            raise ValueError('source identity differs')
        row = matches[0]
        amounts = {f: value(row, f) * (10000 if f == 'FN439' else 1)
                   for f in ('FN41', 'FN55', 'FN52', 'FN56', 'FN439')}
        subtotal = sum(amounts.values())
        if subtotal <= 0:
            raise ValueError('subtotal must be positive')
        def measure(amount):
            return dict(amount_cny=None if amount is None else str(amount),
                        percent_of_subtotal=None if amount is None else str(amount / subtotal * 100))
        interests = [Decimal(c['amount_cny']) for c in base['components']
                     if c['classification'] == 'explicit_interest' and c['amount_cny'] is not None]
        p = payable.get((code, period), {})
        extra = None
        if code == '000035':
            if period == '2022-12-31':
                extra = Decimal(parents[cnty][1]['additional_2022_financing_claims_cny'])
            else:
                extra = Decimal(next(r['values'][1] for r in parents[cnty][0]['rows'] if r['key'] == 'payables'))
        results.append(dict(code=code, period=period, source_components_cny={f: str(v) for f, v in amounts.items()},
            common_field_subtotal_cny=str(subtotal),
            separately_reported_interest=measure(sum(interests) if interests else None),
            unclassified_current_payable=measure(Decimal(p['unclassified_current_payable_cny']) if p else None),
            related_party_narrative_gap=measure(Decimal(p['narrative_less_current_and_noncurrent_cny'])
                                              if 'narrative_less_current_and_noncurrent_cny' in p else None),
            identified_additional_financing_claims=measure(extra)))
    return dict(results=results, full_debt=None, invested_capital=None, economic_roic=None,
        boundary='six_companies_two_FY; 2023-09-01_China_cutoff; later_acquired_not_strict_PIT; '
        'source_precision_subtotal_not_full_debt_or_lower_bound; ratios_not_valuation_impacts; '
        'unknown_not_zero; claim_scope_differs_by_year; overlapping_diagnostics_not_additive')


if __name__ == '__main__':
    print(json.dumps(diagnose(), ensure_ascii=False, indent=2))
