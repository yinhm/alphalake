"""批量核算标准资本分量及口径缺口；候选不写回历史事实或原生估值输入。"""
import argparse
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
import gzip
import itertools
import json
from pathlib import Path
import re
import resource
import subprocess
import tempfile
import time

from tools.export_alphalake_sqlite import digest, record_facts, validate_dates
from tools.review_profit_asset_scope import identity_check

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
FLOW_CONTEXT = ('right_of_use_depreciation', 'investment_property_depreciation_amortization', 'capital_expenditure_cash',
    'revenue_cumulative', 'research_and_development_expense', 'investment_income', 'fair_value_change_income',
    'income_tax_expense', 'profit_before_tax', 'operating_profit_cumulative',
    'associates_and_joint_ventures_investment_income', 'investment_income_cash', 'net_exposure_hedging_income')
BALANCE_CONTEXT = ('other_receivables', 'other_payables', 'other_current_assets',
    'noncurrent_assets_due_within_one_year', 'notes_payable', 'derivative_financial_assets',
    'long_term_equity_investments', 'debt_investments', 'other_debt_investments', 'other_equity_instrument_investments')
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


def depreciation_scope_review(facts, calculations, conflict):
    """源差额只用于诊断，不能从相等、零或缺项批准完整折旧。"""
    core = [field for field, _ in FLOW_FORMULAS['core_depreciation_amortization_components']]
    context = ('right_of_use_depreciation', 'investment_property_depreciation_amortization')
    terms = [('reported_ebitda', 1), ('reported_ebit', -1), *((field, -1) for field in core)]
    fields = [field for field, _ in terms]
    present = [facts[f] for f in (*fields, *context) if f in facts]
    same_record = len({(r['source_record_id'], r['source_filing_id'], r['artifact_sha256']) for r in present}) <= 1
    positive = [f for f in (*core, *context) if f in facts and Decimal(facts[f]['value']) > 0]
    difference = calculations['reported_ebitda_less_ebit']['value_cny']
    identity = dict(status='not_compared', residual_cny=None, rounding_bound_cny=None)
    status = 'missing_components_or_difference'
    if conflict or not same_record:
        status = 'source_record_conflict'
    elif difference is not None and Decimal(difference) == 0 and positive:
        status = 'zero_difference_with_positive_depreciation'
    elif all(f in facts for f in fields):
        period = present[0]['period']
        identity = identity_check({(f, r['period']): Decimal(r['value']) for f, r in facts.items()},
            [(f, period, c) for f, c in terms])
        status = ('core_components_reconciled_scope_unverified' if identity['status'] == 'within_source_rounding'
            else 'reported_difference_has_unexplained_components')
    return dict(status=status, reported_ebitda_less_ebit_cny=difference,
        positive_standard_components=positive, missing_comparison_fields=[f for f in fields if f not in facts],
        arithmetic_identity=identity, valuation_approved=False,
        boundary='EBITDA与EBIT差额为零不证明折旧为零；核心分量相等不证明已含使用权或投资性房地产。合并列示须依原文确认范围，不按字段有无猜总额')


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
        available = datetime.fromisoformat(row['available_at']) if row['available_at'] is not None else None
        amount = Decimal(row['value'])
        if (available is not None and available.utcoffset() is None) or not amount.is_finite():
            raise ValueError('invalid standard amount or announcement timezone')
        if not row['source_record_id'] or not row['artifact_sha256']:
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
    records, indices, values = [], {}, {}
    shared = ('instrument_id', 'source_record_id', 'source_filing_id', 'period', 'available_at', 'artifact_sha256',
              'announcement_source', 'source_observed_at')
    for field, fact in facts.items():
        key = tuple(fact.get(k) for k in shared)
        if key not in indices:
            indices[key] = len(records)
            records.append({k:fact.get(k) for k in shared})
        values[field] = dict(value_cny=fact['value'], record_index=indices[key])
    return dict(period=period.isoformat(), calculations=calculations,
        depreciation_scope_review=depreciation_scope_review(facts, calculations, conflict),
        working_capital_scope_review=dict(cashflow_contribution_cny=calculations['cashflow_working_capital_contribution']['value_cny'],
            liability_classification_fields=[f for f in ('current_portion_noncurrent_liabilities','notes_payable',
                'other_payables') if f in facts and Decimal(facts[f]['value']) != 0],
            receivable_classification_fields=[f for f in ('other_receivables','noncurrent_assets_due_within_one_year')
                if f in facts and Decimal(facts[f]['value']) != 0],
            cashflow_contribution_is_change_in_noncash_wc=False, valuation_approved=False,
            boundary='CFO调节不是经营余额变动；往来、票据及一年内到期负债的经营/融资分类未闭合，不自动当有息债务或营运资本，合并金融业务另审'),
        ebitda_difference_less_core_components_cny=residual,
        residual_boundary='source_precision_retained; equality_is_not_independent_scope_validation',
        context={f: facts.get(f, {}).get('value') for f in (*FLOW_CONTEXT, *BALANCE_CONTEXT)},
        source_evidence=dict(records=records, field_values=values, unit='CNY', statement_scope='provider_default',
            financial_time_basis='current_standard_snapshot_not_pit',
            announcement_policy='TDX_date_if_known; optional_CNINFO; date_does_not_block_current_amounts'),
        valuation_inputs=dict(d_a=None, noncash_wc=None, change_in_noncash_wc=None, historical_fcff=None),
        valuation_approved=False, unresolved=BOUNDARIES)


def audit_export(data, codes, periods, cutoff):
    """消费当前宽行导出；只保留一个公司的所选期间。"""
    companies = {}
    with (data/'companies.jsonl').open() as stream:
        for line in stream:
            company = json.loads(line)
            symbols = company.get('symbols') or []
            if len(symbols) == 1 and company.get('symbol_count') == 1 and company.get('identifier_count') == 1:
                if company['exchange_mic'] in ('XSHG', 'XSHE') and re.fullmatch(r'(?:sh|sz)[0-9]{6}', symbols[0]):
                    if symbols[0][:2] != {'XSHG': 'sh', 'XSHE': 'sz'}[company['exchange_mic']]:
                        raise ValueError('incompatible exchange identity')
                    code = symbols[0][2:]
                    if code in companies:
                        raise ValueError('ambiguous exported company')
                    companies[code] = company
    with (data/'conflicts.jsonl').open() as stream:
        conflicts = {(row['code'],row['period']) for row in map(json.loads,stream)}
    with (data/'fields.jsonl').open() as stream:
        definitions = list(map(json.loads,stream))
    wanted = {p.isoformat() for p in periods}
    with (data/'records.jsonl').open() as stream:
        groups = iter(itertools.groupby(record_facts(stream, definitions), key=lambda r:r['code']))
        current = next(groups, None)
        for code in sorted(codes):
            if current and current[0] < code:
                raise ValueError('unexpected exported company')
            grouped = {}
            if current and current[0] == code:
                for fact in current[1]:
                    if fact['period'] in wanted:
                        grouped.setdefault(fact['period'], []).append(fact)
                current = next(groups, None)
            for period in sorted(periods):
                company = companies.get(code)
                row = dict(code=code, period=period.isoformat(), status='blocked_security_identity')
                if company:
                    row.update(status='audited_not_valuation_approved', **derive(grouped.get(period.isoformat(), []),
                        company['instrument_id'], period, cutoff, (code,period.isoformat()) in conflicts))
                yield row
        if current is not None:
            raise ValueError('unexpected exported company')


def main():
    started = time.monotonic()
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
        subprocess.run(command, check=True, timeout=600)
        counts, reconciliation = Counter(), Counter()
        complete = Counter({name:0 for name in FLOW_FORMULAS | BALANCE_FORMULAS})
        stream = (gzip.open(args.output, 'xt', compresslevel=1) if args.output.suffix == '.gz' else args.output.open('x'))
        try:
            with stream:
                stream.write('{"results":[\n')
                for index, row in enumerate(audit_export(data, codes, args.period, args.as_of)):
                    if index:
                        stream.write(',\n')
                    json.dump(row, stream, ensure_ascii=False, allow_nan=False)
                    counts[row['status']] += 1
                    if 'depreciation_scope_review' in row:
                        reconciliation[row['depreciation_scope_review']['status']] += 1
                    complete.update(name for name, value in row.get('calculations', {}).items()
                        if value['status'] == 'arithmetic_complete_not_valuation_approved')
                if digest(args.database) != source_hash or Path(str(args.database)+'.wal').exists():
                    raise ValueError('source changed during export')
                report = dict(contract='tdx-capital-derivation-v2', source_database_sha256=source_hash,
                    information_as_of=args.as_of.isoformat(), codes=codes, periods=[p.isoformat() for p in args.period],
                    positions=len(codes)*len(args.period), source_fields=FIELDS,
                    statuses=dict(counts), calculation_complete_counts=dict(complete),
                    depreciation_reconciliation_statuses=dict(reconciliation),
                    source_evidence_catalog=[json.loads(line) for line in (data/'fields.jsonl').read_text().splitlines()],
                    execution=dict(elapsed_seconds=time.monotonic()-started,
                        python_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
                        export_peak_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss*1024,
                        measurement='serial narrow-column export and streamed audit; peaks are separate processes'),
                    source_command=command, implementation_sha256=digest(Path(__file__)), automatic_adoption=False)
                stream.write('\n],'+json.dumps(report, ensure_ascii=False, allow_nan=False)[1:]+'\n')
        except Exception:
            args.output.unlink(missing_ok=True)
            raise


if __name__ == '__main__':
    main()
