"""AlphaLake v1 输入适配：标准查询优先，显式附注独立供给，政策不回写源事实。"""
from datetime import date, datetime, timedelta
from decimal import Decimal
import hashlib
import json
import math
import struct
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from data_sources.alphalake_wacc import WACCBinding, resolve_wacc
from engine.data_dictionary import (CompanyValuationInput, PreparedTTM, RawFinancials,
    MacroInputs, IndustryData, MethodologyChoices, ValuationAssumptions, EquityBridgeInputs)


class Snapshot(BaseModel):
    model_config = ConfigDict(extra='forbid')
    contract_version: Literal['alphalake-valuation-v1']
    code: str = Field(pattern=r'^\d{6}$')
    report_period: date
    information_as_of: datetime
    facts: list[dict]
    windows: list[dict]
    supplements: list[dict]

    @model_validator(mode='after')
    def check_identity(self):
        if self.information_as_of.utcoffset() is None or self.information_as_of.date() < self.report_period:
            raise ValueError('timezone-aware ASOF after report period required')
        if self.report_period.month % 3 or (self.report_period + timedelta(days=1)).day != 1:
            raise ValueError('quarter-end report period required')
        identities = set()
        for r in self.facts + self.windows + self.supplements:
            if r.get('code') != self.code:
                raise ValueError('mixed security identity')
            if r.get('available_at') is not None:
                if not isinstance(r['available_at'],str):
                    raise ValueError('invalid disclosure time')
                at = datetime.fromisoformat(r['available_at'])
                if at.utcoffset() is None or at > self.information_as_of:
                    raise ValueError('future or ambiguous disclosure time')
            if 'instrument_id' in r:
                if not isinstance(r['instrument_id'],int) or r['instrument_id'] <= 0:
                    raise ValueError('invalid instrument identity')
                identities.add(r['instrument_id'])
        if len(identities) > 1:
            raise ValueError('ambiguous historical security identity')
        return self


class Policy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    policy_id: Literal['anker-consolidated-v1', 'moutai-liquor-proxy-v1']
    approved_report_period: date
    scenario: str = Field(min_length=1)
    review_note: str = Field(min_length=1)
    parameters: dict[str, float]

    @model_validator(mode='after')
    def validate_parameters(self):
        common = {'growth', 'margin', 'tax_start', 'tax_terminal', 'terminal_growth',
                  'terminal_roic', 'sales_to_capital', 'operating_cash_ratio'}
        extra = ({'investment_recovery', 'cash_other_recovery', 'minority_multiple', 'debt_multiple', 'extra_dilution_rate'}
                 if self.policy_id.startswith('anker') else
                 {'financial_asset_recovery', 'risk_asset_recovery', 'finance_pb', 'minority_scale', 'finance_ownership'})
        p = self.parameters
        if set(p) not in (common | extra, common | extra | {'wacc'}) or not all(math.isfinite(v) for v in p.values()):
            raise ValueError('policy requires exactly the explicit finite parameters')
        if not (0 <= p['terminal_growth'] < p['terminal_roic'] <= 1) or ('wacc' in p and not p['terminal_growth'] < p['wacc'] < 1):
            raise ValueError('invalid terminal growth/WACC/ROIC')
        for key in ['margin', 'tax_start', 'tax_terminal', 'operating_cash_ratio']:
            if not 0 <= p[key] <= 1:
                raise ValueError('invalid policy ratio: '+key)
        if not -1 < p['growth'] <= 1 or p['sales_to_capital'] <= 0:
            raise ValueError('invalid growth or sales-to-capital')
        for key in extra:
            if p[key] < 0 or (('recovery' in key or key == 'finance_ownership') and p[key] > 1):
                raise ValueError('invalid bridge parameter: '+key)
        return self


class AlphaLakeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    data: Snapshot
    policy: Policy
    wacc_binding: WACCBinding | None = None

    @model_validator(mode='after')
    def wacc_source(self):
        if (self.wacc_binding is None) != ('wacc' in self.policy.parameters):
            raise ValueError('provide either direct WACC or reference binding, never both or neither')
        return self


class MissingInputs(ValueError):
    def __init__(self, items):
        self.items = sorted(set(items))
        super().__init__('missing required valuation inputs: '+', '.join(self.items))


def content_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def build_inputs(request: AlphaLakeRequest):
    d, policy = request.data, request.policy
    anker = policy.policy_id == 'anker-consolidated-v1'
    if d.code != ('300866' if anker else '600519') or policy.approved_report_period != d.report_period:
        raise ValueError('policy is not approved for this security/report period')
    p = dict(policy.parameters)
    reference_components = None
    reference_audit = None
    end = d.report_period.isoformat()
    prior = d.report_period.replace(year=d.report_period.year-1).isoformat()
    annual = f'{d.report_period.year-1}-12-31'
    fact_ids = {r['fact_id']: r for r in d.facts}
    if len(fact_ids) != len(d.facts):
        raise ValueError('duplicate source fact identity')
    windows = {r['field']: r for r in d.windows}
    notes = {(r['period'], r['item']): r for r in d.supplements}
    if len(windows) != len(d.windows) or len(notes) != len(d.supplements):
        raise ValueError('duplicate valuation inputs')
    consumed = []

    def amount(r):
        v = Decimal(r['value'])
        if not v.is_finite():
            raise ValueError('non-finite source value')
        return float(v / (1 if r['unit'] == 'CNY/share' else 1000000))

    def window(field, required=True):
        r = windows.get(field)
        if not r or r.get('coverage_status') != 'complete' or r.get('value') is None:
            if required:
                raise MissingInputs(['TDX/'+field])
            return None
        unit = 'share' if field == 'FN238' else 'CNY'
        if r['unit'] != unit or r['statement_scope'] != 'provider_default':
            raise ValueError('unsupported standard unit/scope: '+field)
        ids, periods, coefficients = r['source_fact_ids'], r['input_periods'], r['input_coefficients']
        if len(ids) != r['required_inputs'] or len(ids) != r['available_inputs'] or len(ids) != len(periods) or len(ids) != len(coefficients):
            raise ValueError('incomplete window lineage: '+field)
        basis = r['calculation_basis']
        if basis == 'instant':
            expected = [(end, 1)]
        elif basis == 'ytd':
            expected = [(end, 1)] if d.report_period.month == 12 else [(end,1),(annual,1),(prior,-1)]
        elif basis == 'quarter':
            cursor = d.report_period
            expected = []
            for _ in range(4):
                expected.append((cursor.isoformat(),1))
                month = cursor.month-2
                cursor = date(cursor.year,month,1)-timedelta(days=1)
        else:
            raise ValueError('unsupported window basis')
        if list(zip(periods,coefficients)) != expected:
            raise ValueError('incorrect window periods/coefficient: '+field)
        total = Decimal(0)
        for fid, period, coefficient in zip(ids,periods,coefficients):
            f = fact_ids.get(fid)
            if not f or f['field'] != field or f['period'] != period or f['unit'] != unit or f['statement_scope'] != r['statement_scope']:
                raise ValueError('window does not match source facts: '+field)
            month = int(period[5:7])
            expected_type = ('instant' if basis == 'instant' and field != 'FN238' else
                             f'Q{month//3}' if basis == 'quarter' else
                             {3:'Q1',6:'H1',9:'9M' if basis == 'ytd' else 'Q3',12:'FY'}[month])
            if f['period_type'] != expected_type or r['period_type'] != ('instant' if basis == 'instant' else 'TTM'):
                raise ValueError('incorrect financial period basis: '+field)
            if not f.get('artifact_sha256') or not f.get('announcement_id'):
                raise ValueError('missing standard provenance')
            if f.get('available_at') is None:
                raise ValueError('missing fact announcement time')
            if f.get('multiplier') not in (1,10000):
                raise ValueError('unreviewed source multiplier')
            if not isinstance(f.get('bits'),int) or not 0 <= f['bits'] <= 0xffffffff:
                raise ValueError('invalid float32 source bits')
            raw = struct.unpack('<f',struct.pack('<I',f['bits']))[0]
            if not math.isfinite(raw) or abs(Decimal.from_float(raw)*Decimal(f['multiplier'])-Decimal(f['value'])) > Decimal('.0000001'):
                raise ValueError('standard value differs from preserved source bits')
            total += Decimal(f['value'])*coefficient
        if abs(total-Decimal(r['value'])) > Decimal('.0000001'):
            raise ValueError('window differs from source components: '+field)
        consumed.append(dict(source='tdx',field=field,source_fact_ids=ids))
        return amount(r)

    def note(item, period=end):
        r = notes.get((period,item))
        if r is None:
            raise MissingInputs(['CNINFO/'+period+'/'+item])
        scope = 'finance_subsidiary' if item.startswith('finance_') else ('convertible_security' if item in ('extra_conversion_price','convertible_face') else 'consolidated_note_component')
        unit = 'CNY/share' if item == 'extra_conversion_price' else 'CNY'
        basis = 'ytd' if item in ('finance_net_income','forward_realized','fv_forward_asset','fv_forward_liability') else 'instant'
        if r.get('available_at') is None or r['unit'] != unit or r['scope'] != scope or r['period_basis'] != basis or not r.get('reviewer') or not r.get('review_note') or not r.get('import_sha256') or not r.get('pdf_sha256') or not r.get('announcement_id') or r.get('pdf_page',0) <= 0:
            raise ValueError('unreviewed or incompatible supplement: '+item)
        consumed.append(dict(source='cninfo',item=item,period=period,announcement_id=r['announcement_id'],pdf_sha256=r['pdf_sha256'],pdf_page=r['pdf_page']))
        return amount(r)

    def note_ttm(item):
        return note(item) if d.report_period.month == 12 else note(item,annual)+note(item)-note(item,prior)

    required_windows = ('FN230 FN86 FN305 FN306 FN83 FN82 FN301 FN238 '+
        ('FN136 FN137 FN138 FN579 FN581 FN8 FN133 FN25 FN69 FN41 FN55 FN56 FN439 FN59 FN299 FN72' if anker else
         'FN506 FN509 FN510 FN520 FN97 FN8 FN403 FN409 FN19 FN411 FN430 FN431 FN433 FN25 FN413 FN52 FN439')).split()
    missing = ['TDX/'+f for f in required_windows if f not in windows or windows[f].get('coverage_status') != 'complete' or windows[f].get('value') is None]
    end_notes = ('extra_restricted_cash extra_current_financial_debt extra_noncurrent_financial_debt deposits extra_current_financial_equity extra_noncurrent_financial_equity loan_receivable loan_allowance income_tax_payable repurchase_payable capex_payable ipo_payable current_loans current_bonds current_leases convertible_face extra_conversion_price'.split()
                 if anker else ['finance_equity','income_tax_payable'])
    flow_notes = ['forward_realized','fv_forward_asset','fv_forward_liability'] if anker else ['finance_net_income']
    required_notes = [(end,k) for k in end_notes]+[(period,k) for k in flow_notes for period in ([end] if d.report_period.month==12 else [annual,end,prior])]
    missing += ['CNINFO/'+period+'/'+k for period,k in required_notes if (period,k) not in notes]
    if missing:
        raise MissingInputs(missing)
    w = {f:window(f) for f in required_windows}
    revenue, shares = w['FN230'], w['FN238']
    if revenue <= 0 or shares <= 0:
        raise ValueError('positive revenue and closing shares required')
    if anker:
        ebit = w['FN86']+w['FN305']-w['FN306']-w['FN83']+note_ttm('forward_realized')-w['FN82']+note_ttm('fv_forward_asset')+note_ttm('fv_forward_liability')-w['FN301']
        da = sum(w[f] for f in ['FN136','FN137','FN138','FN579','FN581'])
        other_cash = w['FN8']-w['FN133']-note('extra_restricted_cash')
        excess = w['FN133']+other_cash*p['cash_other_recovery']-revenue*p['operating_cash_ratio']
        if excess < 0 or other_cash < 0:
            raise ValueError('cash classification or operating reserve not supported')
        investments = note('extra_current_financial_debt')+note('extra_noncurrent_financial_debt')+note('deposits')
        risky = note('extra_current_financial_equity')+note('extra_noncurrent_financial_equity')+w['FN25']+note('loan_receivable')-note('loan_allowance')
        convertible = w['FN56']+note('current_bonds')
        debt = w['FN41']+w['FN55']+w['FN56']+w['FN439']+sum(note(k) for k in ['current_loans','current_bonds','current_leases'])
        claims = sum(note(k) for k in ['income_tax_payable','repurchase_payable','capex_payable','ipo_payable'])+w['FN59']
        components = dict(excess_cash=excess,financial_assets_after_haircut=investments+risky*p['investment_recovery'],
            debt_claim_proxy=-debt*p['debt_multiple'],minority_claim_proxy=-w['FN69']*p['minority_multiple'],
            convertible_option_book_proxy=-w['FN299'],existing_other_claims=-claims)
        conversion_price, face = note('extra_conversion_price'), note('convertible_face')
        if conversion_price <= 0 or face <= 0:
            raise ValueError('positive conversion price and remaining face required')
        bridge = EquityBridgeInputs(policy_id=policy.policy_id,components=components,operating_ownership=1,
            shares=shares*(1+p['extra_dilution_rate']),conversion_release=convertible*p['debt_multiple']+w['FN299'],conversion_shares=face/conversion_price)
        raw = RawFinancials(fiscal_year=d.report_period.year,revenues=revenue,ebit=ebit,d_a=da,
            capex=window('FN114',False),r_and_d_expense=window('FN304',False),bv_equity=w['FN72'],
            bv_debt=debt,cash_and_marketable_securities=w['FN133'],minority_interests=w['FN69'],shares_outstanding=shares)
    else:
        ebit = w['FN86']-w['FN506']+w['FN509']+w['FN510']-w['FN83']-w['FN82']-w['FN520']-w['FN301']+w['FN305']-w['FN306']
        if ebit <= 0:
            raise ValueError('liquor minority proxy requires positive EBIT')
        # 沿用已审核的 25% NOPAT 分母代理，不宣称精确去合并。
        minority = (w['FN97']-note_ttm('finance_net_income')*(1-p['finance_ownership']))/(ebit*.75)*p['minority_scale']
        if not 0 <= minority < 1:
            raise ValueError('invalid operating minority proxy')
        ownership = 1-minority
        finance_equity = note('finance_equity')
        pool = dict(financial_gross=sum(w[f] for f in ['FN8','FN403','FN409','FN19','FN411','FN430','FN431'])*p['financial_asset_recovery'],
            risk_assets=(w['FN433']+w['FN25'])*p['risk_asset_recovery'],external_deposits=-w['FN413'],
            full_finance_book_equity_removed=-finance_equity,operating_cash_reserve=-revenue*p['operating_cash_ratio'],
            income_tax_payable=-note('income_tax_payable'),lease_debt=-w['FN52']-w['FN439'])
        components = {k:v*ownership for k,v in pool.items()}
        components['owned_finance_equity_value'] = finance_equity*p['finance_ownership']*p['finance_pb']
        bridge = EquityBridgeInputs(policy_id=policy.policy_id,components=components,operating_ownership=ownership,
            shares=shares,conversion_release=0,conversion_shares=0)
        raw = RawFinancials(fiscal_year=d.report_period.year,revenues=revenue,ebit=ebit,shares_outstanding=shares)
    if request.wacc_binding is not None:
        reference_components, reference_audit = resolve_wacc(request.wacc_binding, d.code, d.report_period, d.information_as_of, ebit=ebit, interest=w['FN305'], debt=debt if anker else w['FN52']+w['FN439'], bridge=bridge)
        p['wacc'] = reference_audit['result']['wacc']
        if p['wacc'] <= p['terminal_growth']:
            raise ValueError('reference WACC must exceed terminal growth')
    assumptions = ValuationAssumptions(projection_years=10,high_growth_years=5,revenue_growth_next_year=p['growth'],revenue_growth_years_2_5=p['growth'],
        operating_margin_next_year=ebit/revenue,target_operating_margin=p['margin'],margin_convergence_year=5,
        sales_to_capital_high=p['sales_to_capital'],sales_to_capital_stable=p['sales_to_capital'],override_reinvestment_lag=True,
        reinvestment_lag_years=0,cost_of_capital_stable_override=p['wacc'],roic_stable_override=p['terminal_roic'],
        override_growth_perpetuity=True,growth_perpetuity_rate=p['terminal_growth'])
    start = d.report_period.replace(year=d.report_period.year-1)+timedelta(days=1)
    inputs = CompanyValuationInput(ticker=d.code,reporting_currency='CNY',stock_price_currency='CNY',fx_rate=1,
        prepared_ttm=PreparedTTM(financials=raw,period_start=start,period_end=d.report_period,information_as_of=d.information_as_of,
            currency='CNY',money_unit='million_reporting_currency',shares_unit='million_shares',
            provenance={'alphalake_snapshot':content_hash(d.model_dump(mode='json')),'valuation_policy':content_hash(policy.model_dump(mode='json'))}),
        equity_bridge=bridge,macro_inputs=MacroInputs(risk_free_rate=.03,equity_risk_premium=.06,tax_rate_effective=p['tax_start'],tax_rate_marginal=p['tax_terminal']),
        industry_data=IndustryData(industry_name='unused_direct_wacc',beta_u=0),methodology_choices=MethodologyChoices(cost_of_capital_approach='direct',wacc_direct_input=p['wacc']),valuation_assumptions=assumptions)
    audit = dict(consumed_inputs=consumed,source='alphalake',
        required_input_count=len(required_windows)+len(required_notes),
        available_required_input_count=len(required_windows)+len(required_notes),
        completeness_scope='only this explicitly approved forecast/bridge policy, not all financial data',
        historical_fcff_status='missing_classification_not_zero',
        policy_status='illustrative_model_not_reported_fact',
        boundaries=['CNY only','reviewed security and report period only','explicit WACC; RF/ERP containers unused by direct WACC',
                    'RD expensed; leases not capitalized twice','book claim proxies; employee options not priced',
                    'uniform sales-to-capital forecast; not original inventory-vintage policy'] + ([] if anker else ['liquor/finance allocation is a policy proxy, not exact deconsolidation']))
    if reference_components is not None:
        inputs.methodology_choices = MethodologyChoices(cost_of_capital_approach='reference_snapshot', reference_capital_inputs=reference_components)
        inputs.macro_inputs.risk_free_rate = reference_components.risk_free_rate
        inputs.macro_inputs.equity_risk_premium = reference_components.mature_market_erp
        audit['wacc_reference'] = reference_audit
        audit['completeness_scope'] += '; reference selection audited separately in wacc_reference'
        audit['boundaries'] = [b for b in audit['boundaries'] if not b.startswith('explicit WACC;')]
        audit['boundaries'] += reference_audit['boundaries']
        inputs.prepared_ttm.provenance['wacc_binding'] = content_hash(request.wacc_binding.model_dump(mode='json'))
    return inputs,audit
