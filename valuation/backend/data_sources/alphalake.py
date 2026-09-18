"""AlphaLake v1 输入适配：标准查询优先，显式附注独立供给，政策不回写源事实。"""
from datetime import date, datetime, timedelta
from decimal import Decimal
import hashlib
import json
import math
import struct
from statistics import median
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from data_sources.alphalake_wacc import WACCBinding, resolve_wacc
from data_sources.alphalake_capital import CapitalBinding, resolve_capital
from data_sources.alphalake_calibration import FirstYearCalibration
from engine.data_dictionary import (CompanyValuationInput, PreparedTTM, RawFinancials,
    MacroInputs, IndustryData, MethodologyChoices, ValuationAssumptions, EquityBridgeInputs, ForecastYear)


class Snapshot(BaseModel):
    model_config = ConfigDict(extra='forbid')
    contract_version: Literal['alphalake-valuation-v1']
    code: str = Field(pattern=r'^\d{6}$')
    report_period: date
    information_as_of: datetime
    facts: list[dict]
    windows: list[dict]
    supplements: list[dict]
    source_conflicts: list[dict] = Field(default_factory=list)

    @model_validator(mode='after')
    def check_identity(self):
        if self.information_as_of.utcoffset() is None or self.information_as_of.date() < self.report_period:
            raise ValueError('timezone-aware ASOF after report period required')
        if self.report_period.month % 3 or (self.report_period + timedelta(days=1)).day != 1:
            raise ValueError('quarter-end report period required')
        identities = set()
        for r in self.facts + self.windows + self.supplements + self.source_conflicts:
            if r.get('code') != self.code:
                raise ValueError('mixed security identity')
            if (provenance := r.get('document_provenance')) is not None:
                reviewed_at = datetime.fromisoformat(provenance['reviewed_at'])
                if reviewed_at.utcoffset() is None or reviewed_at > self.information_as_of:
                    raise ValueError('future or ambiguous document review time')
                if (provenance['code'] != self.code or provenance['period'] != r.get('period')
                        or provenance['announcement_id'] != r.get('announcement_id')
                        or provenance['canonical_url'] != r.get('pdf_url')
                        or provenance['sha256'] != r.get('pdf_sha256')):
                    raise ValueError('document review identity differs')
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
    policy_id: Literal['anker-consolidated-v1', 'anker-consolidated-v2', 'moutai-liquor-proxy-v1']
    approved_report_period: date
    scenario: str = Field(min_length=1)
    review_note: str = Field(min_length=1)
    parameters: dict[str, float]
    annual_forecast: list[ForecastYear] | None = None
    debt_basis: Literal['financial_book','wacc_estimate'] = 'financial_book'
    capital_basis: Literal['financial_date','disclosed_share_scenario'] = 'financial_date'
    capital_carry_reason: str | None = Field(default=None,min_length=1)

    @model_validator(mode='after')
    def validate_parameters(self):
        common = {'growth', 'margin', 'tax_start', 'tax_terminal', 'terminal_growth',
                  'terminal_roic', 'sales_to_capital', 'operating_cash_ratio'}
        extra = ({'investment_recovery', 'cash_other_recovery', 'minority_multiple', 'debt_multiple', 'extra_dilution_rate'}
                 if self.policy_id.startswith('anker') else
                 {'financial_asset_recovery', 'risk_asset_recovery', 'finance_pb', 'minority_scale', 'finance_ownership'})
        revised = self.policy_id == 'anker-consolidated-v2'
        if revised:
            if self.annual_forecast is None or len(self.annual_forecast)!=10:
                raise ValueError('revised Anker policy requires all ten forecast years')
            common -= {'growth','margin','tax_start','tax_terminal'}
            extra = (extra-{'minority_multiple'})|{'minority_earnings_multiple'}
        elif self.annual_forecast is not None or self.debt_basis!='financial_book' or self.capital_basis!='financial_date' or self.capital_carry_reason is not None:
            raise ValueError('versioned revised policy required for forecast/debt changes')
        if (self.capital_basis=='disclosed_share_scenario')!=(self.capital_carry_reason is not None):
            raise ValueError('disclosed-share scenario requires explicit financial/conversion carry reason')
        p = self.parameters
        if set(p) not in (common | extra, common | extra | {'wacc'}) or not all(math.isfinite(v) for v in p.values()):
            raise ValueError('policy requires exactly the explicit finite parameters')
        if not (0 <= p['terminal_growth'] < p['terminal_roic'] <= 1) or ('wacc' in p and not p['terminal_growth'] < p['wacc'] < 1):
            raise ValueError('invalid terminal growth/WACC/ROIC')
        for key in (['operating_cash_ratio'] if revised else ['margin', 'tax_start', 'tax_terminal', 'operating_cash_ratio']):
            if not 0 <= p[key] <= 1:
                raise ValueError('invalid policy ratio: '+key)
        if (not revised and not -1 < p['growth'] <= 1) or p['sales_to_capital'] <= 0:
            raise ValueError('invalid growth or sales-to-capital')
        if self.debt_basis=='wacc_estimate' and p['debt_multiple']!=1:
            raise ValueError('estimated debt cannot also receive a book multiplier')
        for key in extra:
            if p[key] < 0 or (('recovery' in key or key == 'finance_ownership') and p[key] > 1):
                raise ValueError('invalid bridge parameter: '+key)
        return self


class ScreenPolicy(BaseModel):
    """普通非金融经营价值情景；不在证券索偿未闭合时输出每股值。"""
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    policy_id: Literal['nonfinancial-earnings-power-v1']
    approved_report_period: date
    scenario: str = Field(min_length=1)
    review_note: str = Field(min_length=1)
    nonfinancial_scope_review: str = Field(min_length=1)
    wacc: float | None = Field(default=None,gt=0,lt=1)
    tax_rate: float = Field(ge=0,le=1)


class BookDCFPolicy(ScreenPolicy):
    """财报日的普通企业账面索偿情景；所有估值取舍显式提供。"""
    policy_id: Literal['nonfinancial-book-fcff-v1']
    annual_forecast: list[ForecastYear] = Field(min_length=10,max_length=10)
    sales_to_capital: float | None = Field(default=None,gt=0)
    terminal_growth: float = Field(ge=0)
    terminal_roic: float = Field(gt=0,le=1)
    cash_recovery: float = Field(ge=0,le=1)
    operating_cash_ratio: float = Field(ge=0,le=1)
    minority_book_multiple: float = Field(ge=0)
    debt_book_multiple: float = Field(gt=0)
    extra_dilution_rate: float = Field(ge=0,le=1)
    additional_claims_million_cny: float = Field(ge=0)
    bridge_basis: Literal['report_date_book_debt_no_conversion_scenario']
    financial_asset_policy: Literal['no_credit_pending_classification']
    bridge_review: str = Field(min_length=1)

    @model_validator(mode='after')
    def check_terminal(self):
        if self.terminal_growth>=self.terminal_roic or (self.wacc is not None and self.terminal_growth>=self.wacc):
            raise ValueError('terminal growth must be below WACC and ROIC')
        return self


class HistoricalDCFPolicy(BookDCFPolicy):
    """历史季度同比驱动的初始预测规则，仍是可覆盖的估值政策。"""
    policy_id: Literal['nonfinancial-history-fcff-v1']
    annual_forecast: None = None
    growth_floor: float = Field(gt=-1,le=1)
    growth_ceiling: float = Field(gt=-1,le=1)
    growth_shift: float = Field(ge=-1,le=1)
    margin_shift: float = Field(ge=-1,le=1)

    @model_validator(mode='after')
    def growth_bounds(self):
        if self.growth_floor>self.growth_ceiling:
            raise ValueError('growth floor exceeds ceiling')
        return self


class CalibratedHistoricalDCFPolicy(HistoricalDCFPolicy):
    policy_id: Literal['nonfinancial-history-fcff-calibrated-v1']
    calibration: FirstYearCalibration

    @model_validator(mode='after')
    def calibration_scope(self):
        if self.calibration.report_period!=self.approved_report_period:
            raise ValueError('calibration report period differs from policy')
        if (self.growth_floor,self.growth_ceiling,self.growth_shift,self.margin_shift)!=(-.1,.2,0,0):
            raise ValueError('calibration requires validated base forecast rules')
        return self


class ReviewedRestrictedComponent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    item: str = Field(pattern=r'^[a-z][a-z0-9_]*$')
    import_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    evidence_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class ReviewedDisposalZero(ReviewedRestrictedComponent):
    item: Literal['reviewed_asset_disposal_cash_zero']
    period: date
    source_artifact_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class ReviewedAssetAddback(BaseModel):
    """标准金融资产分类审核；受限分量须另有同期间、同原文证据。"""
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    field: Literal['FN9', 'FN19', 'FN25', 'FN430', 'FN431', 'FN433']
    item: str = Field(pattern=r'^[a-z][a-z0-9_]*$')
    import_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_artifact_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    evidence_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    classification: Literal['nonoperating_unrestricted_financial_asset', 'nonoperating_partially_restricted_financial_asset', 'nonconsolidated_equity_holding']
    restricted_component: ReviewedRestrictedComponent | None = None
    valuation_basis: Literal['reported_book_value_proxy']
    recovery: float = Field(ge=0, le=1)
    review_note: str = Field(min_length=1)


class ReviewedHistoricalDCFPolicy(HistoricalDCFPolicy):
    policy_id: Literal['nonfinancial-reviewed-history-fcff-v1']
    financial_asset_policy: Literal['reviewed_standard_asset_addbacks']
    code: str = Field(pattern=r'^\d{6}$')
    reviewed_at: datetime
    valid_until: datetime
    asset_addbacks: list[ReviewedAssetAddback] = Field(min_length=1)
    disposal_cash_zeros: list[ReviewedDisposalZero] = Field(default_factory=list)

    @model_validator(mode='after')
    def reviewed_scope(self):
        if self.reviewed_at.utcoffset() is None or self.valid_until.utcoffset() is None or self.valid_until < self.reviewed_at:
            raise ValueError('aware ordered review validity required')
        if len({r.field for r in self.asset_addbacks}) != len(self.asset_addbacks) or len({r.item for r in self.asset_addbacks}) != len(self.asset_addbacks):
            raise ValueError('duplicate reviewed asset field or evidence')
        evidence_items = [r.item for r in self.asset_addbacks]
        for r in self.asset_addbacks:
            partial = r.classification == 'nonoperating_partially_restricted_financial_asset'
            if partial != (r.restricted_component is not None):
                raise ValueError('partial asset classification requires restricted component evidence only')
            if partial:
                if r.field not in ('FN19', 'FN431'):
                    raise ValueError('restricted components currently support FN19/FN431 only')
                evidence_items.append(r.restricted_component.item)
            if (r.field == 'FN25') != (r.classification == 'nonconsolidated_equity_holding'):
                raise ValueError('equity holding classification requires FN25 only')
        if len({r.period for r in self.disposal_cash_zeros}) != len(self.disposal_cash_zeros):
            raise ValueError('duplicate disposal zero period')
        if len(set(evidence_items)) != len(evidence_items):
            raise ValueError('duplicate reviewed asset component evidence')
        return self


class AlphaLakeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    data: Snapshot
    policy: Policy | ScreenPolicy | BookDCFPolicy | HistoricalDCFPolicy | CalibratedHistoricalDCFPolicy | ReviewedHistoricalDCFPolicy
    wacc_binding: WACCBinding | None = None
    capital_binding: CapitalBinding | None = None

    @model_validator(mode='after')
    def wacc_source(self):
        if isinstance(self.policy, BookDCFPolicy):
            if (self.capital_binding is None) == (self.policy.sales_to_capital is None):
                raise ValueError('provide either direct sales-to-capital or reference binding, never both or neither')
        elif self.capital_binding is not None:
            raise ValueError('capital reference requires generic book/history FCFF policy')
        if isinstance(self.policy, ScreenPolicy):
            if (self.wacc_binding is None) == (self.policy.wacc is None):
                raise ValueError('provide either direct WACC or reference binding, never both or neither')
            if self.wacc_binding is not None and (self.wacc_binding.policy.scope != 'consolidated' or self.wacc_binding.policy.market is not None or self.wacc_binding.market_capital is not None):
                raise ValueError('generic WACC requires consolidated target-weight references; market bridge not reviewed')
            return self
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


def amount(r):
    v = Decimal(r['value'])
    if not v.is_finite():
        raise ValueError('non-finite source value')
    return float(v / (1 if r['unit'] == 'CNY/share' else 1000000))


def validated_source_value(f):
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
    return Decimal(f['value'])


def standard_window_reader(d: Snapshot):
    """消费标准窗口前核验源位、期间、单位及完整血缘；供各估值政策复用。"""
    end = d.report_period.isoformat()
    prior = d.report_period.replace(year=d.report_period.year-1).isoformat()
    annual = f'{d.report_period.year-1}-12-31'
    fact_ids = {r['fact_id']: r for r in d.facts}
    if len(fact_ids) != len(d.facts):
        raise ValueError('duplicate source fact identity')
    windows = {r['field']: r for r in d.windows}
    if len(windows) != len(d.windows):
        raise ValueError('duplicate valuation inputs')
    consumed = []

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
            validated_source_value(f)
            total += Decimal(f['value'])*coefficient
        if abs(total-Decimal(r['value'])) > Decimal('.0000001'):
            raise ValueError('window differs from source components: '+field)
        consumed.append(dict(source='tdx',field=field,source_fact_ids=ids))
        return amount(r)

    return window, consumed


def build_inputs(request: AlphaLakeRequest):
    if request.data.source_conflicts:
        raise MissingInputs(['source_record_conflict:'+r['period'] for r in request.data.source_conflicts])
    d, policy = request.data, request.policy
    if isinstance(policy, ScreenPolicy):
        capital_audit = None
        if request.capital_binding is not None:
            ratio,capital_audit=resolve_capital(request.capital_binding,d.code,d.report_period,d.information_as_of)
            policy=type(policy).model_validate(policy.model_dump() | {'sales_to_capital':ratio})
        reference_components = reference_audit = None
        if request.wacc_binding is not None:
            window,_ = standard_window_reader(d)
            ebit = window('FN86')+window('FN305')-window('FN306')-window('FN83')-window('FN82')-window('FN301')
            reference_components,reference_audit = resolve_wacc(request.wacc_binding,d.code,d.report_period,d.information_as_of,ebit=ebit,interest=window('FN305'))
            policy = type(policy).model_validate(policy.model_dump() | {'wacc':reference_audit['result']['wacc']})
        if isinstance(policy, HistoricalDCFPolicy):
            inputs,audit = build_historical_dcf_inputs(d,policy)
        elif isinstance(policy, BookDCFPolicy):
            inputs,audit = build_book_dcf_inputs(d,policy)
        else:
            inputs,audit = build_earnings_power_inputs(d,policy)
        if isinstance(policy, ReviewedHistoricalDCFPolicy):
            apply_reviewed_assets(d, policy, inputs, audit)
            if policy.disposal_cash_zeros:
                apply_reviewed_disposal_zeros(d, policy, audit)
        if capital_audit is not None:
            audit['capital_reference']=capital_audit
            audit['boundaries']+=capital_audit['boundaries']
            inputs.prepared_ttm.provenance['capital_binding']=content_hash(request.capital_binding.model_dump(mode='json'))
        if reference_components is not None:
            inputs.methodology_choices = MethodologyChoices(cost_of_capital_approach='reference_snapshot',reference_capital_inputs=reference_components)
            inputs.macro_inputs.risk_free_rate = reference_components.risk_free_rate
            inputs.macro_inputs.equity_risk_premium = reference_components.mature_market_erp
            inputs.prepared_ttm.provenance['wacc_binding'] = content_hash(request.wacc_binding.model_dump(mode='json'))
            audit['wacc_reference'] = reference_audit
            audit['boundaries'] = [b for b in audit['boundaries'] if not b.startswith('WACC/tax are')]
            audit['boundaries'] += reference_audit['boundaries'] + ['tax, industry/country exposure and target capital weights remain explicit policy; not fully observed company WACC']
        return inputs,audit
    anker = policy.policy_id.startswith('anker-')
    revised = policy.policy_id == 'anker-consolidated-v2'
    if d.code != ('300866' if anker else '600519') or policy.approved_report_period != d.report_period:
        raise ValueError('policy is not approved for this security/report period')
    p = dict(policy.parameters)
    if revised:
        first,last=policy.annual_forecast[0],policy.annual_forecast[-1]
        p.update(growth=first.growth,margin=last.margin,tax_start=first.tax,tax_terminal=last.tax)
    reference_components = None
    reference_audit = None
    end = d.report_period.isoformat()
    prior = d.report_period.replace(year=d.report_period.year-1).isoformat()
    annual = f'{d.report_period.year-1}-12-31'
    window, consumed = standard_window_reader(d)
    windows = {r['field']: r for r in d.windows}
    notes = {(r['period'], r['item']): r for r in d.supplements}
    if len(notes) != len(d.supplements):
        raise ValueError('duplicate valuation inputs')

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
    if revised: required_windows.append('FN97')
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
        minority_claim = w['FN97']*p['minority_earnings_multiple'] if revised else w['FN69']*p['minority_multiple']
        if minority_claim<0: raise ValueError('negative minority earnings cannot value the claim with this policy')
        components = dict(excess_cash=excess,financial_assets_after_haircut=investments+risky*p['investment_recovery'],
            debt_claim_proxy=-debt*p['debt_multiple'],minority_claim_proxy=-minority_claim,
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
        reference_components, reference_audit = resolve_wacc(request.wacc_binding, d.code, d.report_period, d.information_as_of, ebit=ebit, interest=w['FN305'], debt=debt if anker else w['FN52']+w['FN439'], bridge=bridge, note=note)
        p['wacc'] = reference_audit['result']['wacc']
        if p['wacc'] <= p['terminal_growth']:
            raise ValueError('reference WACC must exceed terminal growth')
    if policy.debt_basis=='wacc_estimate':
        market = reference_audit.get('market_capital') if reference_audit else None
        debt_value = market.get('debt_valuation') if market else None
        if not debt_value or debt_value['selected_bound']!='upper':
            raise ValueError('reviewed contractual debt upper bound required for equity bridge')
        bridge.components['debt_claim_proxy']=-float(debt_value['upper_bound_million_cny'])
        bond=debt_value['components']['bond']
        rate=Decimal(1)+Decimal(str(debt_value['discount_rate']))
        bond_pv=sum(Decimal(bond[i])/rate**t for i,t in enumerate((0,1,2,5)))
        bridge.conversion_release=float(bond_pv)+w['FN299']
    capital_audit=None
    if revised:
        market=reference_audit.get('market_capital') if reference_audit else None
        funding=market.get('funding_cash_scenario') if market else None
        if policy.capital_basis=='disclosed_share_scenario':
            if funding is None or policy.debt_basis!='wacc_estimate':
                raise ValueError('disclosed-share scenario requires paired funding evidence and debt valuation')
            disclosed=sum(Decimal(c['shares']) for c in market['classes'])/1000000
            bridge.shares=float(disclosed)*(1+p['extra_dilution_rate'])
            bridge.components['post_report_funding_cash_scenario']=float(funding['cash_adjustment_million_cny'])
            capital_audit=dict(status='disclosed_shares_and_assumed_cash_not_complete_rollforward',
                disclosed_shares_million=str(disclosed),employee_dilution_assumption=p['extra_dilution_rate'],
                funding_cash=funding,conversion_terms_period=end,carry_reason=policy.capital_carry_reason)
        elif funding is not None:
            raise ValueError('revised valuation must update shares and funding cash together')
    assumptions = ValuationAssumptions(annual_forecast=policy.annual_forecast,projection_years=10,high_growth_years=5,revenue_growth_next_year=p['growth'],revenue_growth_years_2_5=p['growth'],
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
    if revised:
        audit['forecast_basis']='reviewed_annual_policy_not_reported_financial_fact'
        audit['capital_date_basis']=policy.capital_basis
        audit['capital_scenario']=capital_audit
        audit['boundaries'].append('conversion terms and non-funding financial balances carried from report date; current fair value is not complete')
        audit['debt_basis']=policy.debt_basis
        audit['minority_basis']='TTM minority income times explicit policy multiple'
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


def build_earnings_power_inputs(d, policy):
    if d.report_period != policy.approved_report_period:
        raise ValueError('screening policy is not approved for report period')
    window, consumed = standard_window_reader(d)
    required = 'FN230 FN86 FN305 FN306 FN83 FN82 FN301'.split()
    values = {f:window(f,False) for f in required}
    missing = ['TDX/'+f for f,v in values.items() if v is None]
    if missing:
        raise MissingInputs(missing)
    # 已知金融业务不得套用普通企业经营价值；缺字段不等于不存在，范围须另行审核。
    for field in ('FN506','FN509','FN510','FN413'):
        value = window(field,False)
        if value is not None and value != 0:
            raise ValueError('financial operations require separate model: '+field)
    revenue = values['FN230']
    ebit = values['FN86']+values['FN305']-values['FN306']-values['FN83']-values['FN82']-values['FN301']
    if revenue <= 0 or ebit <= 0:
        raise ValueError('positive revenue and adjusted EBIT required for earnings-power screen')
    margin=ebit/revenue
    if margin>1:
        raise ValueError('EBIT exceeds revenue; review operating scope')
    raw=RawFinancials(fiscal_year=d.report_period.year,revenues=revenue,ebit=ebit)
    assumptions=ValuationAssumptions(projection_years=10,high_growth_years=5,
        annual_forecast=[ForecastYear(growth=0,margin=margin,tax=policy.tax_rate) for _ in range(10)],
        sales_to_capital_high=1,sales_to_capital_stable=1,override_reinvestment_lag=True,reinvestment_lag_years=0,
        override_growth_perpetuity=True,growth_perpetuity_rate=0,
        roic_stable_override=policy.wacc,cost_of_capital_stable_override=policy.wacc)
    inputs=CompanyValuationInput(ticker=d.code,reporting_currency='CNY',stock_price_currency='CNY',fx_rate=1,
        prepared_ttm=PreparedTTM(financials=raw,period_start=d.report_period.replace(year=d.report_period.year-1)+timedelta(days=1),
            period_end=d.report_period,information_as_of=d.information_as_of,currency='CNY',
            money_unit='million_reporting_currency',shares_unit='million_shares',
            provenance={'alphalake_snapshot':content_hash(d.model_dump(mode='json')),'valuation_policy':content_hash(policy.model_dump(mode='json'))}),
        macro_inputs=MacroInputs(risk_free_rate=0,equity_risk_premium=0,tax_rate_effective=policy.tax_rate,tax_rate_marginal=policy.tax_rate),
        industry_data=IndustryData(industry_name='unused_direct_wacc',beta_u=0),
        methodology_choices=MethodologyChoices(cost_of_capital_approach='direct',wacc_direct_input=policy.wacc),
        valuation_assumptions=assumptions)
    audit=dict(consumed_inputs=consumed,source='alphalake',policy_status='illustrative_model_not_reported_fact',
        historical_fcff_status='missing_classification_not_zero',
        valuation_scope='operating_enterprise_value_only_no_equity_bridge',
        required_input_count=len(required),available_required_input_count=len(required),
        automatic_drivers=dict(revenue=revenue,adjusted_ebit=ebit,operating_margin=margin),
        assumptions=dict(nominal_growth=0,net_reinvestment=0,tax=policy.tax_rate,wacc=policy.wacc),
        boundaries=['nonfinancial scope requires explicit review; missing financial-business fields do not prove absence',
            'zero nominal growth and constant margin scenario, not growth forecast',
            'maintenance capex equals depreciation is an assumption, not historical FCFF closure',
            'investment/fair-value/disposal income excluded; operating hedges not restored',
            'RD remains expensed; reported interest may not consistently include lease finance costs',
            'no equity value or per-share price until cash/debt/minority/dilution bridge is reviewed',
            'WACC/tax are explicit sensitivity assumptions, not market estimates'])
    return inputs,audit


def build_book_dcf_inputs(d, policy):
    inputs,audit=build_earnings_power_inputs(d,policy)
    window,consumed=standard_window_reader(d)
    fields='FN238 FN133 FN41 FN52 FN55 FN56 FN439 FN69'.split()
    values={f:window(f,False) for f in fields}
    missing=['TDX/'+f for f,v in values.items() if v is None]
    if missing: raise MissingInputs(missing)
    if values['FN238']<=0 or any(values[f]<0 for f in fields):
        raise ValueError('positive shares and nonnegative book claims/cash required')
    raw=inputs.prepared_ttm.financials
    rd, cash_capex = window('FN304',False), window('FN114',False)
    audit['company_capital_evidence'] = dict(source='tdx_standard_ttm',
        rd_expense_million_cny=rd, rd_to_revenue=rd/raw.revenues if rd is not None else None,
        cash_capex_million_cny=cash_capex,
        boundary='研发费用及购建现金仅为投入分量；不代表完整净再投资，不能直接推出收入增长或公司边际资本效率。')
    # 旧快照无此字段时保持旧诊断；新标准链的缺期不能被当作零处置。
    if any(w['field'] == 'FN110' for w in d.windows):
        disposal = window('FN110', False)
        audit['company_capital_evidence'].update(
            asset_disposal_cash_million_cny=disposal,
            cash_capex_after_disposals_million_cny=(cash_capex-disposal
                if cash_capex is not None and disposal is not None else None),
            cash_capex_after_disposals_status=('cash_component_not_total_reinvestment'
                if cash_capex is not None and disposal is not None else 'missing_standard_cash_components'))
    raw.shares_outstanding=values['FN238']
    debt=sum(values[f] for f in ('FN41','FN52','FN55','FN56','FN439'))
    components=dict(cash_recovery_scenario=values['FN133']*policy.cash_recovery,
        operating_cash_reserve=-raw.revenues*policy.operating_cash_ratio,
        debt_book_proxy=-debt*policy.debt_book_multiple,
        minority_book_proxy=-values['FN69']*policy.minority_book_multiple,
        additional_claims_scenario=-policy.additional_claims_million_cny)
    inputs.equity_bridge=EquityBridgeInputs(policy_id=policy.policy_id,components=components,operating_ownership=1,
        shares=values['FN238']*(1+policy.extra_dilution_rate),conversion_release=0,conversion_shares=0)
    inputs.valuation_assumptions=ValuationAssumptions(projection_years=10,high_growth_years=5,
        annual_forecast=policy.annual_forecast,sales_to_capital_high=policy.sales_to_capital,
        sales_to_capital_stable=policy.sales_to_capital,override_reinvestment_lag=True,reinvestment_lag_years=0,
        override_growth_perpetuity=True,growth_perpetuity_rate=policy.terminal_growth,
        roic_stable_override=policy.terminal_roic,cost_of_capital_stable_override=policy.wacc)
    audit['consumed_inputs']+=consumed
    audit['required_input_count']+=len(fields);audit['available_required_input_count']+=len(fields)
    audit['valuation_scope']='report_date_book_equity_scenario'
    audit['assumptions']=dict(annual_forecast=[r.model_dump() for r in policy.annual_forecast],
        sales_to_capital=policy.sales_to_capital,terminal_growth=policy.terminal_growth,
        terminal_roic=policy.terminal_roic,wacc=policy.wacc,bridge=policy.model_dump(mode='json',exclude={'annual_forecast'}))
    audit['boundaries']=[b for b in audit['boundaries'] if not b.startswith(('zero nominal','maintenance capex','no equity value'))]
    audit['boundaries'] += ['report-date shares and book claims; not a current-date securities rollforward',
        'all FN52 treated as debt proxy without maturity-note split',
        'nonoperating financial investments receive no credit pending classification; not asserted zero',
        'no-conversion scenario only; convertible choice and employee options not priced',
        'additional claims amount is a policy scenario, not a claim that undisclosed obligations are absent',
        'cash recovery and minority/debt multiples are assumptions, not fair-value observations']
    return inputs,audit


def historical_forecast(revenue, ebit, quarters, report_period, policy):
    """共享纯预测规则；quarters只含调用方已校验的起点可用收入及来源引用。"""
    if revenue<=0 or ebit<=0:
        raise ValueError('positive revenue and EBIT required for historical forecast rules')
    pairs=[]
    for period,(current,current_id) in sorted(quarters.items()):
        if period>report_period or period<=report_period.replace(year=report_period.year-1):continue
        previous=quarters.get(period.replace(year=period.year-1))
        if previous is not None and previous[0]>0 and current>=0:
            pairs.append(dict(period=period.isoformat(),growth=float(current/previous[0]-1),source_fact_ids=[current_id,previous[1]]))
    if len(pairs)<2:
        raise MissingInputs(['TDX/at_least_two_same_quarter_revenue_yoy_pairs'])
    observed=median(r['growth'] for r in pairs)
    growth=max(policy.growth_floor,min(policy.growth_ceiling,observed+policy.growth_shift))
    margin=ebit/revenue;target=margin+policy.margin_shift
    if not 0<target<=1:raise ValueError('rule-generated target margin outside (0,1]')
    annual=[ForecastYear(growth=growth if year<=5 else growth+(policy.terminal_growth-growth)*(year-5)/5,
        margin=margin+(target-margin)*min(year/5,1),tax=policy.tax_rate) for year in range(1,11)]
    evidence=dict(required_yoy_pairs=2,available_yoy_pairs=len(pairs),revenue_yoy_pairs=pairs,
                        observed_median_growth=observed,clipped_scenario_growth=growth,
                        current_adjusted_margin=margin,target_margin=target)
    if isinstance(policy,CalibratedHistoricalDCFPolicy):
        before=annual[0].margin
        annual[0]=ForecastYear(growth=annual[0].growth,margin=before*policy.calibration.multiplier,tax=annual[0].tax)
        evidence['first_year_calibration']=dict(model_id=policy.calibration.model_id,multiplier=policy.calibration.multiplier,
            margin_before=before,margin_after=annual[0].margin,training_pairs=len(policy.calibration.observations),
            evidence_basis=policy.calibration.evidence_basis,policy_sha256=content_hash(policy.calibration.model_dump(mode='json')))
    return annual,evidence


def build_historical_dcf_inputs(d, policy):
    if isinstance(policy,CalibratedHistoricalDCFPolicy):
        if d.code not in policy.calibration.approved_codes or policy.calibration.prepared_at>d.information_as_of:
            raise ValueError('calibration not approved or not yet available for valuation')
    window,_=standard_window_reader(d)
    revenue=window('FN230')
    ebit=window('FN86')+window('FN305')-window('FN306')-window('FN83')-window('FN82')-window('FN301')
    if revenue<=0 or ebit<=0:
        raise ValueError('positive revenue and EBIT required for historical forecast rules')
    quarters={}
    for f in d.facts:
        if f['field']!='FN230':continue
        period=date.fromisoformat(f['period'])
        if period>d.report_period:continue
        if period.month%3 or (period+timedelta(days=1)).day!=1 or f['period_type']!=f'Q{period.month//3}' or f['unit']!='CNY' or f['statement_scope']!='provider_default':
            raise ValueError('invalid quarterly revenue history')
        if period in quarters:raise ValueError('duplicate quarterly revenue history')
        quarters[period]=(validated_source_value(f),f['fact_id'])
    annual,evidence=historical_forecast(revenue,ebit,quarters,d.report_period,policy)
    parameters=policy.model_dump(exclude={'policy_id','annual_forecast','growth_floor','growth_ceiling','growth_shift','margin_shift','calibration','code','reviewed_at','valid_until','asset_addbacks','disposal_cash_zeros'})
    if isinstance(policy, ReviewedHistoricalDCFPolicy):
        parameters['financial_asset_policy'] = 'no_credit_pending_classification'
    generated=BookDCFPolicy(policy_id='nonfinancial-book-fcff-v1',annual_forecast=annual,**parameters)
    inputs,audit=build_book_dcf_inputs(d,generated)
    inputs.prepared_ttm.provenance['assumption_rules']=content_hash(policy.model_dump(mode='json'))
    audit['generated_policy']=generated.model_dump(mode='json')
    audit['forecast_rule_evidence']=evidence
    if isinstance(policy,CalibratedHistoricalDCFPolicy):
        audit['boundaries'].append('research-estimated calibration changes first-year EBIT only; later years and terminal assumptions are unvalidated baseline policies, not calibrated forecasts')
    audit['consumed_inputs'].append(dict(source='tdx',field='FN230',purpose='historical_growth_rule',
        source_fact_ids=sorted({i for r in evidence['revenue_yoy_pairs'] for i in r['source_fact_ids']})))
    audit['boundaries'].append('median recent quarterly YOY with policy cap/shift and five-year fade; mechanical starting scenario, not researched growth forecast')
    return inputs,audit


def apply_reviewed_assets(d, policy, inputs, audit):
    """绑定标准源版本与已入库原文补充，阻止旧审核随修订金额自动沿用。"""
    if policy.code != d.code or policy.approved_report_period != d.report_period:
        raise ValueError('asset review security/report period differs')
    if not policy.reviewed_at <= d.information_as_of <= policy.valid_until:
        raise ValueError('asset review unavailable or expired at cutoff')
    window, consumed = standard_window_reader(d)
    facts = {r['fact_id']: r for r in d.facts}
    windows = {r['field']: r for r in d.windows}
    adopted = []
    for rule in policy.asset_addbacks:
        value = window(rule.field)
        if value < 0:
            raise ValueError('negative reviewed financial asset')
        w = windows[rule.field]
        if w['calculation_basis'] != 'instant' or len(w['source_fact_ids']) != 1:
            raise ValueError('asset addback requires one period-end source fact')
        f = facts[w['source_fact_ids'][0]]
        def reviewed_note(binding):
            matches = [r for r in d.supplements if r['period'] == d.report_period.isoformat() and r['item'] == binding.item]
            if not matches:
                raise MissingInputs(['CNINFO/'+d.report_period.isoformat()+'/'+binding.item])
            if len(matches) != 1:
                raise ValueError('ambiguous asset review supplement')
            r = matches[0]
            if (r.get('unit'), r.get('period_basis'), r.get('scope')) != ('CNY', 'instant', 'consolidated_note_component'):
                raise ValueError('incompatible asset review supplement scope/unit/period')
            if (content_hash(r) != binding.evidence_sha256 or r.get('import_sha256') != binding.import_sha256 or f['artifact_sha256'] != rule.source_artifact_sha256
                    or not r.get('reviewer') or not r.get('review_note') or not r.get('available_at')
                    or not r.get('pdf_sha256') or r.get('pdf_page', 0) <= 0
                    or r.get('announcement_id') != f['announcement_id'] or r.get('pdf_sha256') != f.get('pdf_sha256')
                    or r.get('pdf_url') != f.get('pdf_url')
                    or r.get('document_provenance') != f.get('document_provenance')):
                raise ValueError('asset review evidence differs from approved current filing/source')
            return r

        r = reviewed_note(rule)
        # PDF整项金额须在源精度下与TDX一致；不把原文小数写回标准金额。
        reported = Decimal(r['value'])
        if not reported.is_finite() or reported < 0:
            raise ValueError('invalid reviewed asset amount')
        bits = struct.unpack('<I', struct.pack('<f', float(reported/Decimal(f['multiplier']))))[0]
        if bits != f['bits']:
            raise ValueError('asset review total differs from standard source precision')
        restricted = reviewed_note(rule.restricted_component) if rule.restricted_component else None
        restricted_value = Decimal(restricted['value']) if restricted else Decimal(0)
        if not restricted_value.is_finite() or restricted_value < 0 or restricted_value > reported or restricted_value > Decimal(str(value))*1000000:
            raise ValueError('invalid reviewed restricted amount')
        eligible = value - float(restricted_value/1000000)
        component = 'reviewed_asset_'+rule.field
        inputs.equity_bridge.components[component] = eligible*rule.recovery
        adopted.append(dict(rule=rule.model_dump(mode='json'), supplement=r,
                            standard_value_million_cny=value, restricted_supplement=restricted,
                            restricted_value_million_cny=float(restricted_value/1000000),
                            eligible_value_million_cny=eligible, adopted_value_million_cny=eligible*rule.recovery,
                            source_fact_id=f['fact_id']))
    inputs.equity_bridge.policy_id = policy.policy_id
    audit['reviewed_assets'] = adopted
    evidence_count = 2*len(adopted)+sum(r['restricted_supplement'] is not None for r in adopted)
    audit['required_input_count'] += evidence_count
    audit['available_required_input_count'] += evidence_count
    audit['consumed_inputs'] += consumed
    audit['consumed_inputs'] += [dict(source=r['supplement'].get('document_provenance', {}).get('retrieval_source', 'cninfo'), item=r['supplement']['item'],
        period=r['supplement']['period'], import_sha256=r['supplement']['import_sha256']) for r in adopted]
    audit['consumed_inputs'] += [dict(source=r.get('document_provenance', {}).get('retrieval_source', 'cninfo'), item=r['item'], period=r['period'],
        import_sha256=r['import_sha256']) for a in adopted if (r := a['restricted_supplement']) is not None]
    audit['assumptions']['bridge'] = policy.model_dump(mode='json')
    audit['boundaries'] = [b for b in audit['boundaries'] if not b.startswith('nonoperating financial investments receive no credit')]
    audit['boundaries'].append('only explicitly reviewed asset fields credited using standard book amounts less separately reviewed restricted components, then policy recoveries; excluded restricted amounts are not assumed permanently lost; remaining assets unreviewed, not zero; book proxies not market valuations')


def apply_reviewed_disposal_zeros(d, policy, audit):
    """只对明确绑定的缺期采用原文审核零；标准窗口及TDX事实保持原样。"""
    end = d.report_period.isoformat()
    periods = [end] if d.report_period.month == 12 else [end, f'{d.report_period.year-1}-12-31', d.report_period.replace(year=d.report_period.year-1).isoformat()]
    coefficients = [1] if len(periods) == 1 else [1, 1, -1]
    windows = [w for w in d.windows if w['field'] == 'FN110']
    if len(windows) != 1:
        raise MissingInputs(['TDX/FN110'])
    w = windows[0]
    bindings = {r.period.isoformat(): r for r in policy.disposal_cash_zeros}
    if (w['calculation_basis'] != 'ytd' or w['unit'] != 'CNY' or w['statement_scope'] != 'provider_default'
            or w['input_periods'] != periods or w['input_coefficients'] != coefficients
            or w['required_inputs'] != len(periods) or len(w['source_fact_ids']) != len(periods)
            or set(w['missing_periods']) != set(bindings)
            or w['available_inputs'] != len(periods)-len(bindings)
            or w['coverage_status'] != 'missing_inputs' or w['value'] is not None):
        raise ValueError('disposal review must bind exactly the missing standard periods')
    total = Decimal(0)
    sources = []
    for period, coefficient, fid in zip(periods, coefficients, w['source_fact_ids'], strict=True):
        facts = [f for f in d.facts if f['period'] == period and f['field'] == 'FN110']
        if period not in bindings:
            if len(facts) != 1 or facts[0]['fact_id'] != fid:
                raise ValueError('disposal standard lineage differs')
            f = facts[0]
            validated_source_value(f)
            basis = {3:'Q1', 6:'H1', 9:'9M', 12:'FY'}[int(period[5:7])]
            if (f['unit'], f['statement_scope'], f['period_type'], f['multiplier']) != ('CNY','provider_default',basis,1):
                raise ValueError('incompatible disposal source scope')
            total += coefficient*Decimal(f['value'])
            sources.append(dict(period=period, coefficient=coefficient, standard_fact=f))
            continue
        if facts or fid is not None:
            raise ValueError('reviewed zero cannot override a standard fact')
        binding = bindings[period]
        notes = [n for n in d.supplements if n['period'] == period and n['item'] == binding.item]
        if not notes:
            raise MissingInputs(['CNINFO/'+period+'/'+binding.item])
        if len(notes) != 1:
            raise ValueError('ambiguous disposal zero review')
        note = notes[0]
        # 同期购建现金事实作为公告和源包锚点，不用另一期PDF补零。
        anchors = [f for f in d.facts if f['period'] == period and f['field'] == 'FN114']
        if len(anchors) != 1:
            raise MissingInputs(['TDX/'+period+'/FN114'])
        anchor = anchors[0]
        validated_source_value(anchor)
        if (note['unit'],note['period_basis'],note['scope']) != ('CNY','ytd','consolidated_note_component') or Decimal(note['value']) != 0:
            raise ValueError('only explicitly reviewed zero disposal cash supported')
        if (content_hash(note) != binding.evidence_sha256 or note['import_sha256'] != binding.import_sha256
                or anchor['artifact_sha256'] != binding.source_artifact_sha256
                or not note.get('reviewer') or not note.get('review_note') or not note.get('available_at')
                or not note.get('pdf_sha256') or note.get('pdf_page',0) <= 0
                or any(note.get(k) != anchor.get(k) for k in ('announcement_id','pdf_sha256','pdf_url','document_provenance'))):
            raise ValueError('disposal review evidence differs from approved filing/source')
        sources.append(dict(period=period, coefficient=coefficient, supplement=note, source_anchor=anchor))
    evidence = audit['company_capital_evidence']
    disposal = float(total/1000000)
    capex = evidence['cash_capex_million_cny']
    evidence.update(reviewed_asset_disposal_cash_million_cny=disposal,
        reviewed_cash_capex_after_disposals_million_cny=capex-disposal if capex is not None else None,
        reviewed_disposal_status='standard_plus_explicit_reviewed_zeros_not_standard_ttm',
        reviewed_disposal_sources=sources)
    audit['consumed_inputs'] += sources
    audit['required_input_count'] += len(sources)+len(bindings)
    audit['available_required_input_count'] += len(sources)+len(bindings)
