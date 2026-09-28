"""批量核算标准资本分量及口径缺口；候选不写回历史事实或原生估值输入。"""
import argparse
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
import re
import subprocess
import tempfile

from tools.export_alphalake_sqlite import digest, validate_dates

FLOW_FORMULAS = {
    'reported_ebitda_less_ebit': (('reported_ebitda', 1), ('reported_ebit', -1)),
    'core_depreciation_amortization_components': (('depreciation_depletion', 1),
        ('intangible_amortization', 1), ('deferred_expense_amortization', 1)),
    'cashflow_working_capital_contribution': (('inventory_decrease_cashflow', 1),
        ('operating_receivables_decrease_cashflow', 1), ('operating_payables_increase_cashflow', 1)),
}
BALANCE_FORMULAS = {
    'accounting_working_capital': (('current_assets', 1), ('current_liabilities', -1)),
    'trade_working_capital_subtotal': (('accounts_receivable', 1), ('inventories', 1), ('accounts_payable', -1)),
    'noncash_working_capital_candidate': (('current_assets', 1), ('monetary_funds', -1),
        ('trading_financial_assets', -1), ('current_liabilities', -1),
        ('short_term_borrowings', 1), ('current_portion_noncurrent_liabilities', 1)),
}
FLOW_CONTEXT = ('right_of_use_depreciation', 'investment_property_depreciation_amortization', 'capital_expenditure_cash')
BALANCE_CONTEXT = ('other_receivables', 'other_payables', 'other_current_assets',
    'noncurrent_assets_due_within_one_year', 'notes_payable', 'derivative_financial_assets')
FLOW_FIELDS = {f for terms in FLOW_FORMULAS.values() for f, _ in terms} | set(FLOW_CONTEXT)
FIELDS = sorted(FLOW_FIELDS | {f for terms in BALANCE_FORMULAS.values() for f, _ in terms} | set(BALANCE_CONTEXT))
BOUNDARIES = {
    'd_a': ['reported_ebitda_scope_not_company_verified', 'depreciation_components_may_overlap',
            'missing_component_not_proven_zero', 'lease_treatment_must_match_ebit_and_reinvestment'],
    'noncash_wc': ['cash_and_financial_asset_classification_incomplete',
                   'current_noncurrent_liabilities_not_all_interest_bearing',
                   'other_operating_balances_require_classification',
                   'cashflow_reconciliation_not_balance_change'],
}


def derive(rows, instrument, period, cutoff, conflict=False):
    validate_dates(period, cutoff)
    facts = {}
    for row in rows:
        field = row['field']
        if field not in FIELDS or field in facts:
            raise ValueError('unknown or duplicate standard field')
        expected = {3: 'Q1', 6: 'H1', 9: '9M', 12: 'FY'}[period.month] if field in FLOW_FIELDS else 'instant'
        if (row['canonical_field'], row['instrument_id'], row['period'], row['unit'], row['period_type'],
                row['statement_scope'], row['source']) != (field, instrument, period.isoformat(), 'CNY', expected, 'provider_default', 'tdx'):
            raise ValueError('incompatible standard identity/unit/period/scope')
        available = datetime.fromisoformat(row['available_at'])
        amount = Decimal(row['value'])
        if available.utcoffset() is None or available > cutoff or not amount.is_finite():
            raise ValueError('future or invalid standard fact')
        if not row['source_record_id'] or not row['source_filing_id'] or not row['artifact_sha256']:
            raise ValueError('source evidence required')
        facts[field] = row
    calculations = {}
    for name, terms in (FLOW_FORMULAS | BALANCE_FORMULAS).items():
        missing = [f for f, _ in terms if f not in facts]
        present = [facts[f] for f, _ in terms if f in facts]
        identities = {(r['source_record_id'], r['source_filing_id'], r['artifact_sha256']) for r in present}
        status = ('source_record_conflict' if conflict else 'mixed_source_records' if len(identities) > 1
                  else 'missing_standard_fact' if missing else 'arithmetic_complete_not_valuation_approved')
        value = sum((Decimal(facts[f]['value'])*c for f, c in terms), Decimal(0)) if status == 'arithmetic_complete_not_valuation_approved' else None
        calculations[name] = dict(value_cny=str(value) if value is not None else None, status=status,
            missing_fields=missing, terms=[dict(field=f, coefficient=c) for f, c in terms])
    left, right = [calculations[k]['value_cny'] for k in ('reported_ebitda_less_ebit', 'core_depreciation_amortization_components')]
    comparison_fields = {f for name in ('reported_ebitda_less_ebit', 'core_depreciation_amortization_components') for f, _ in FLOW_FORMULAS[name]}
    same_record = len({(facts[f]['source_record_id'], facts[f]['source_filing_id'], facts[f]['artifact_sha256']) for f in comparison_fields if f in facts}) == 1
    residual = str(Decimal(left)-Decimal(right)) if same_record and left is not None and right is not None else None
    return dict(period=period.isoformat(), calculations=calculations,
        ebitda_difference_less_core_components_cny=residual,
        residual_boundary='source_precision_retained; equality_is_not_independent_scope_validation',
        context={f: facts.get(f, {}).get('value') for f in (*FLOW_CONTEXT, *BALANCE_CONTEXT)},
        source_evidence=facts,
        valuation_inputs=dict(d_a=None, noncash_wc=None, change_in_noncash_wc=None, historical_fcff=None),
        valuation_approved=False, unresolved=BOUNDARIES)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--codes', required=True, help='逗号分隔的沪深六位代码，含无事实证券分母')
    parser.add_argument('--period', action='append', type=date.fromisoformat, required=True)
    parser.add_argument('--as-of', type=datetime.fromisoformat, required=True)
    parser.add_argument('--alphalake', type=Path, default=Path(__file__).resolve().parents[3]/'alphalake')
    args = parser.parse_args()
    codes = args.codes.split(',')
    if len(set(codes)) != len(codes) or any(not re.fullmatch(r'\d{6}', c) for c in codes):
        parser.error('unique six-digit codes required')
    if len(set(args.period)) != len(args.period) or args.output.exists():
        parser.error('unique periods and new output required')
    for period in args.period:
        validate_dates(period, args.as_of)
    if Path(str(args.database)+'.wal').exists():
        raise ValueError('checkpointed source required')
    source_hash = digest(args.database)
    with tempfile.TemporaryDirectory(prefix='alphalake-capital-') as temp:
        data = Path(temp)/'facts'
        command = [str(args.alphalake.resolve()), 'export-financial-snapshot', str(args.database.resolve()),
            '--output', str(data), '--codes', args.codes, '--fields', ','.join(FIELDS),
            '--from', min(args.period).isoformat(), '--period', max(args.period).isoformat(), '--as-of', args.as_of.isoformat()]
        subprocess.run(command, check=True)
        packets = {name: [json.loads(line) for line in (data/(name+'.jsonl')).read_text().splitlines()]
                   for name in ('companies', 'facts', 'conflicts')}
    if digest(args.database) != source_hash or Path(str(args.database)+'.wal').exists():
        raise ValueError('source changed during export')
    companies = {}
    for company in packets['companies']:
        symbols = company.get('symbols') or []
        if len(symbols) == 1 and company.get('symbol_count') == 1 and company.get('identifier_count') == 1:
            if company['exchange_mic'] in ('XSHG', 'XSHE') and re.fullmatch(r'(?:sh|sz)[0-9]{6}', symbols[0]):
                if symbols[0][:2] != {'XSHG': 'sh', 'XSHE': 'sz'}[company['exchange_mic']]:
                    raise ValueError('incompatible exchange identity')
                code = symbols[0][2:]
                if code in companies:
                    raise ValueError('ambiguous exported company')
                companies[code] = company
    grouped = {}
    for row in packets['facts']:
        if row['code'] not in codes:
            raise ValueError('unexpected exported company')
        grouped.setdefault((row['code'], row['period']), []).append(row)
    conflicts = {(r['code'], r['period']) for r in packets['conflicts']}
    results = []
    for code in codes:
        for period in args.period:
            company = companies.get(code)
            row = dict(code=code, period=period.isoformat(), status='blocked_security_identity')
            if company:
                row.update(status='audited_not_valuation_approved', **derive(grouped.get((code,period.isoformat()), []),
                    company['instrument_id'], period, args.as_of, (code,period.isoformat()) in conflicts))
            results.append(row)
    report = dict(contract='tdx-capital-derivation-v1', source_database_sha256=source_hash,
        information_as_of=args.as_of.isoformat(), codes=codes, periods=[p.isoformat() for p in args.period],
        positions=len(results), source_fields=FIELDS, results=results,
        statuses=dict(Counter(r['status'] for r in results)),
        calculation_complete_counts={name: sum(r.get('calculations', {}).get(name, {}).get('status') ==
            'arithmetic_complete_not_valuation_approved' for r in results) for name in FLOW_FORMULAS | BALANCE_FORMULAS},
        source_command=command, implementation_sha256=digest(Path(__file__)), automatic_adoption=False)
    with args.output.open('x') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


if __name__ == '__main__':
    main()
