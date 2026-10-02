"""已批准的报表信用附加条件；不替换用户输入或原生评级算法。"""
from datetime import date, datetime
from decimal import Decimal

from data_sources.alphalake_wacc import ReferenceSnapshot
from engine.data_dictionary import CompanyValuationInput, ReferenceCapitalInputs
from engine.module_2_risk import compute_reference_cost_of_capital

POLICY_VERSION = 'reported-statement-credit-conditions-v1'
FIELDS = ('reported_ebit', 'interest_expense', 'financial_business_interest_income',
          'financial_business_interest_expense', 'lease_liabilities', 'right_of_use_assets')


def load_evidence(connection, tickers, metadata, policy):
    if set(policy) != {'version', 'references', 'sovereign_spread_policy', 'max_credit_age_days', 'reason'} or policy['version'] != POLICY_VERSION:
        raise ValueError('explicit reported credit policy required')
    if policy['sovereign_spread_policy'] not in ('add_cn_default_spread', 'none') or not isinstance(policy['reason'], str) or not policy['reason'].strip():
        raise ValueError('sovereign spread policy and reason required')
    if type(policy['max_credit_age_days']) is not int or policy['max_credit_age_days'] < 0:
        raise ValueError('explicit nonnegative credit reference age required')
    refs = ReferenceSnapshot.model_validate(policy['references'])
    cutoff = datetime.fromisoformat(metadata['information_as_of'])
    if refs.information_as_of != cutoff or not refs.credit_spreads:
        raise ValueError('credit reference cutoff mismatch or missing bands')
    sovereign = next(r for r in refs.country_risk if r['subject_code']=='CN' and r['metric_code']=='sovereign_default_spread')
    published = connection.execute("SELECT value,release_id,observation_date FROM reference_value WHERE subject='CN' AND metric='sovereign_default_spread'").fetchall()
    if len(published)!=1 or tuple(published[0]) != (sovereign['value'],sovereign['release_id'],sovereign['observation_date']):
        raise ValueError('credit country reference differs from SQLite publication')
    if not connection.execute("SELECT 1 FROM standard_facts WHERE field IN ('financial_business_interest_income','financial_business_interest_expense') LIMIT 1").fetchone():
        raise ValueError('SQLite credit scope fields not exported; rebuild snapshot before credit conditions')
    end = date.fromisoformat(metadata['report_period'])
    terms = [(end, 1)] if end.month==12 else [(date(end.year-1,12,31),1),(end,1),(end.replace(year=end.year-1),-1)]
    marks = ','.join('?' for _ in tickers)
    field_marks = ','.join('?' for _ in FIELDS)
    period_marks = ','.join('?' for _ in terms)
    rows = {ticker:[] for ticker in tickers}
    if tickers:
        for row in connection.execute(f"SELECT * FROM standard_facts WHERE ticker IN ({marks}) AND field IN ({field_marks}) AND period IN ({period_marks})",[*tickers,*FIELDS,*[p.isoformat() for p,_ in terms]]):
            rows[row['ticker']].append(dict(row))
    return dict(terms=[(p.isoformat(),c) for p,c in terms], rows=rows,
        bands=refs.credit_spreads, sovereign=sovereign, cutoff=cutoff.isoformat())


def compare(baseline, evidence, policy):
    inputs = CompanyValuationInput.model_validate(baseline['inputs'])
    row = dict(version=POLICY_VERSION, status='outside_policy_scope', automatic_adoption=False,
        source_facts=evidence['rows'].get(inputs.ticker, []), variants={},
        boundary='US大非金融表的跨市场报表代理，非正式评级；已入表租赁不重复计入；不认证经营范围、信用或未来风险')
    def reject(reason):
        row['reason']=reason
        return row
    if inputs.reporting_currency!='CNY' or inputs.country!='China' or inputs.methodology_choices.cost_of_capital_approach!='reference_snapshot':
        return reject('requires_explicit_CNY_China_reference_capital_inputs')
    if inputs.adjustment_inputs.has_operating_leases:
        return reject('additional_operating_lease_adjustment_requires_matched_credit_scope')
    end = evidence['terms'][-2][0] if len(evidence['terms'])==3 else evidence['terms'][0][0]
    if max(filter(None,(inputs.period_date_10k,inputs.period_date_10q)))[:10] != end:
        return reject('financial_report_period_mismatch')
    facts={}
    for fact in row['source_facts']:
        key=(fact['period'],fact['field'])
        if key in facts: return reject('duplicate_standard_credit_fact')
        value=Decimal(fact['value'])
        expected={3:'Q1',6:'H1',9:'9M',12:'FY'}[date.fromisoformat(fact['period']).month]
        if fact['unit']!='CNY' or fact['statement_scope']!='provider_default' or fact['period_type']!=('instant' if fact['field'] in ('lease_liabilities','right_of_use_assets') else expected) or not value.is_finite():
            return reject('incompatible_standard_credit_fact')
        facts[key]=value
    if any(v>0 for (p,f),v in facts.items() if f.startswith('financial_business_interest_')):
        return reject('financial_business_interest_components_observed')
    totals={}
    for field in ('reported_ebit','interest_expense'):
        if any((p,field) not in facts for p,c in evidence['terms']):
            return reject('missing_standard_credit_components')
        totals[field]=sum((facts[p,field]*c for p,c in evidence['terms']),Decimal(0))
    if totals['interest_expense']<=0: return reject('nonpositive_gross_interest')
    # 只接受与标准报表对应的原利润；不从用户覆盖/研发调整利润选信用。
    if abs(Decimal(str(baseline['ltm_financials']['ebit']))-totals['reported_ebit']/Decimal(1000000))>Decimal('0.000001'):
        return reject('model_reported_ebit_differs_from_standard_basis')
    coverage=totals['reported_ebit']/totals['interest_expense']
    bands=[b for b in evidence['bands'] if Decimal(b['coverage_lower'])<coverage<=Decimal(b['coverage_upper'])]
    if len(bands)!=1: return reject('official_credit_interval_gap_or_conflict')
    band=bands[0]
    age=(datetime.fromisoformat(evidence['cutoff']).date()-date.fromisoformat(band['observation_date'])).days
    if age<0 or age>policy['max_credit_age_days']:return reject('credit_reference_outside_explicit_age_limit')
    row.update(status='conditional_proxy',coverage_ratio=str(coverage),diagnostic_band=band,reference_age_days=age,
        sovereign_spread_policy=policy['sovereign_spread_policy'], reason=policy['reason'],
        lease_scope='capitalized_lease_components_observed' if any(v>0 for (p,f),v in facts.items() if f in ('lease_liabilities','right_of_use_assets')) else 'no_positive_lease_marker_not_absence_proof')
    for name in ('initial_credit_only','hold_current_wacc_and_no_terminal_excess_return'):
        changed=inputs.model_copy(deep=True)
        components=changed.methodology_choices.reference_capital_inputs
        if components is None:return reject('missing_reference_capital_components')
        sovereign=Decimal(evidence['sovereign']['value']) if policy['sovereign_spread_policy']=='add_cn_default_spread' else Decimal(0)
        components.debt_cost_pretax=float(Decimal(str(components.risk_free_rate))+sovereign+Decimal(band['value']))
        components.debt_cost_basis='analyst_credit_reference'
        wacc=compute_reference_cost_of_capital(ReferenceCapitalInputs.model_validate(components.model_dump())).wacc
        if name=='hold_current_wacc_and_no_terminal_excess_return':
            changed.valuation_assumptions.cost_of_capital_stable_override=wacc
            changed.valuation_assumptions.roic_stable_override=wacc
        row['variants'][name]=dict(inputs=changed.model_dump(mode='json'))
    return row
