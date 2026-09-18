"""固定发布版本的 WACC 输入选择；事实、选择理由和目标资本结构分开保存。"""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from engine.data_dictionary import ReferenceCapitalInputs
from data_sources.alphalake_market import MarketPolicy, MarketSnapshot, equity_market_value, contractual_debt_value
from engine.module_2_risk import compute_reference_cost_of_capital


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class IndustryWeight(Strict):
    industry: str = Field(min_length=1)
    weight: float = Field(gt=0, le=1)
    reason: str = Field(min_length=1)


class CountryWeight(Strict):
    country: Literal['CN', 'HK', 'US']
    weight: float = Field(gt=0, le=1)
    exposure_scale: float = Field(ge=0)
    reason: str = Field(min_length=1)


class SyntheticDebtPolicy(Strict):
    firm_type: Literal['large_nonfinancial']
    coverage_basis: Literal['policy_operating_ebit_over_tdx_gross_interest']
    applicability_reason: str = Field(min_length=1)
    sovereign_spread_policy: Literal['add_cn_default_spread', 'none']
    sovereign_spread_reason: str = Field(min_length=1)
    max_credit_age_days: int = Field(ge=0)


class CreditBandDebtPolicy(Strict):
    rating: Literal['Aaa/AAA','Aa2/AA','A1/A+','A2/A','A3/A-','Baa2/BBB']
    selection_reason: str = Field(min_length=1)
    sovereign_spread_policy: Literal['add_cn_default_spread','none']
    sovereign_spread_reason: str = Field(min_length=1)
    max_credit_age_days: int = Field(ge=0)


class WACCPolicy(Strict):
    policy_id: str = Field(min_length=1)
    code: str = Field(pattern=r'^[0-9]{6}$')
    report_period: date
    scope: Literal['consolidated', 'liquor_proxy']
    currency: Literal['CNY']
    review_note: str = Field(min_length=1)
    industries: list[IndustryWeight] = Field(min_length=1)
    countries: list[CountryWeight] = Field(min_length=1)
    weighting_basis: Literal['explicit_analyst_weights']
    beta_metric: Literal['beta_unlevered', 'beta_unlevered_cash_adjusted']
    minimum_sample_count: int = Field(gt=0)
    government_tenor_months: Literal[3, 6, 12, 36, 60, 84, 120, 360]
    risk_free_method: Literal['government_proxy', 'subtract_cn_default_spread']
    risk_free_reason: str = Field(min_length=1)
    capital_structure_basis: Literal['target_weights','industry_reference_weights','market_equity_estimated_debt']
    target_debt_weight: float | None = Field(default=None,ge=0, lt=1)
    market: MarketPolicy | None = None
    capital_structure_reason: str = Field(min_length=1)
    debt_cost_pretax: float | None = Field(default=None, ge=0, lt=1)
    synthetic_debt: SyntheticDebtPolicy | None = None
    credit_band_debt: CreditBandDebtPolicy | None = None
    debt_cost_reason: str = Field(min_length=1)
    tax_shield_rate: float = Field(ge=0, le=1)
    tax_shield_reason: str = Field(min_length=1)
    stable_wacc_policy: Literal['hold_current']
    max_yield_age_days: int = Field(ge=0)
    max_beta_age_days: int = Field(ge=0)
    max_country_age_days: int = Field(ge=0)

    @model_validator(mode='after')
    def weights(self):
        if self.capital_structure_basis=='target_weights':
            if self.target_debt_weight is None or self.market is not None: raise ValueError('target weight required without market policy')
        elif self.capital_structure_basis=='industry_reference_weights':
            if self.target_debt_weight is not None or self.market is not None: raise ValueError('industry weights must be derived without target override or market policy')
        elif self.market is None or self.target_debt_weight is not None: raise ValueError('market weights must be derived; no target weight')
        if sum(x is not None for x in [self.synthetic_debt,self.debt_cost_pretax,self.credit_band_debt])!=1:
            raise ValueError("exactly one explicit, synthetic or analyst credit-band debt cost required")
        for rows, key in [(self.industries, 'industry'), (self.countries, 'country')]:
            if len({getattr(r, key) for r in rows}) != len(rows) or abs(sum(r.weight for r in rows)-1) > 1e-9:
                raise ValueError('unique explicit weights summing to one required')
        return self


class ReferenceSnapshot(Strict):
    contract_version: Literal['alphalake-wacc-references-v1', 'alphalake-wacc-references-v2']
    information_as_of: datetime
    recorded_cutoff: datetime | None
    releases: list[dict]
    country_risk: list[dict]
    industry_stats: list[dict]
    yield_curve: list[dict]
    credit_spreads: list[dict] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_packet(self):
        try:
            return self._validate_packet()
        except (KeyError, TypeError, InvalidOperation) as error:
            raise ValueError('malformed reference packet: '+str(error)) from error

    def _validate_packet(self):
        if self.information_as_of.utcoffset() is None or (self.recorded_cutoff is not None and self.recorded_cutoff.utcoffset() is None):
            raise ValueError('aware reference cutoffs required')
        self.information_as_of = self.information_as_of.astimezone(timezone.utc)
        for release in self.releases:
            if any(type(release[k]) is not int or release[k] <= 0 for k in ('release_id', 'artifact_id')):
                raise ValueError('positive integer reference IDs required')
        releases = {r['release_id']: r for r in self.releases}
        count = 4 if self.contract_version.endswith("v2") else 3
        if (count == 3 and self.credit_spreads) or len(releases) != count or len(self.releases) != count:
            raise ValueError('reference release count does not match contract version')
        expected = [('country_risk', 'damodaran', 'country-risk-cn-hk-us-rating-v1', 10),
                    ('industry_stats', 'damodaran', 'global-industry-beta-2026-v1', 376),
                    ('yield_curve', 'chinabond', 'cny-government-eight-tenors-v1', 8)]
        if count == 4:
            expected.append(('credit_spreads', 'damodaran', 'synthetic-credit-large-2026-v1', 15))
        for field, source, dataset, count in expected:
            rows = getattr(self, field)
            selected = [r for r in self.releases if (r['source'], r['dataset']) == (source, dataset)]
            if len(selected) != 1 or len(rows) != count or len({r['observation_id'] for r in rows}) != count:
                raise ValueError('incomplete/duplicate reference scope')
            release = selected[0]
            for hash_key in ('content_key', 'artifact_sha256'):
                if not re.fullmatch('[0-9a-f]{64}', release[hash_key]):
                    raise ValueError('invalid reference provenance hash')
            available = datetime.fromisoformat(release['available_at'])
            recorded = datetime.fromisoformat(release['recorded_at'])
            if available.utcoffset() is None or recorded.utcoffset() is None or available > self.information_as_of:
                raise ValueError('reference unavailable at information cutoff')
            if self.recorded_cutoff is not None and recorded > self.recorded_cutoff:
                raise ValueError('reference not yet recorded')
            for r in rows:
                if r['release_id'] != release['release_id'] or r['artifact_id'] != release['artifact_id']:
                    raise ValueError('reference row provenance mismatch')
                if date.fromisoformat(r['observation_date']) > self.information_as_of.date():
                    raise ValueError('future reference observation')
                if not isinstance(r['value'], str) or not re.fullmatch(r'-?\d+\.\d{12}', r['value']):
                    raise ValueError('canonical decimal reference value required')
                value, raw = Decimal(r['value']), Decimal(r['raw_value'])
                if not raw.is_finite() or abs(raw/(100 if field in ('yield_curve', 'credit_spreads') else 1)-value) > Decimal('0.0000000000005'):
                    raise ValueError('reference raw/standard mismatch')
                if field == 'credit_spreads':
                    lo, hi = Decimal(r['coverage_lower']), Decimal(r['coverage_upper'])
                    if (not lo.is_finite() or not hi.is_finite() or lo >= hi or lo != Decimal(r['raw_lower']) or hi != Decimal(r['raw_upper'])
                            or r['observation_precision'] != 'month' or r['observation_date'] != '2026-01-01'
                            or r['firm_type'] != 'large_nonfinancial' or r['method_code'] != 'us_synthetic_rating_source_open_closed'
                            or r['raw_unit'] != 'percent' or not 0 <= value < 1):
                        raise ValueError('invalid credit band semantics')
                if field in ('country_risk', 'industry_stats') and r['value_status'] != 'reported':
                    raise ValueError('missing reference value')
        if self.credit_spreads:
            ordered = sorted(self.credit_spreads,key=lambda r: Decimal(r['coverage_lower']))
            ratings = ['D2/D','C2/C','Ca2/CC','Caa/CCC','B3/B-','B2/B','B1/B+','Ba2/BB','Ba1/BB+','Baa2/BBB','A3/A-','A2/A','A1/A+','Aa2/AA','Aaa/AAA']
            if ([r['rating'] for r in ordered] != ratings or
                    any(Decimal(a['coverage_upper']) >= Decimal(b['coverage_lower']) for a,b in zip(ordered,ordered[1:]))):
                raise ValueError('credit rating scope/overlap mismatch')
        return self


class WACCBinding(Strict):
    references: ReferenceSnapshot
    policy: WACCPolicy
    market_capital: MarketSnapshot | None = None


def resolve_wacc(binding: WACCBinding, code: str, period: date, information_as_of: datetime, *, ebit: float | None = None, interest: float | None = None, debt: float | None = None, bridge=None, note=None):
    s, p = binding.references, binding.policy
    expected_scope = 'liquor_proxy' if code == '600519' else 'consolidated'
    if p.code != code or p.report_period != period or p.scope != expected_scope or s.information_as_of != information_as_of:
        raise ValueError('WACC/company/scope/period/cutoff mismatch')
    information_as_of = information_as_of.astimezone(timezone.utc)
    used = []

    def select(rows, age, **criteria):
        matches = [r for r in rows if all(r.get(k) == v for k, v in criteria.items())]
        if len(matches) != 1:
            raise ValueError('missing/ambiguous WACC reference: '+str(criteria))
        row = matches[0]
        days = (information_as_of.date()-date.fromisoformat(row['observation_date'])).days
        if days < 0 or days > age:
            raise ValueError('stale/future WACC reference: '+str(criteria))
        used.append(row)
        return row, float(Decimal(row['value']))

    _, government = select(s.yield_curve, p.max_yield_age_days, currency='CNY', curve_code='chinabond_government_pbc',
                           tenor_months=p.government_tenor_months, rate_type='yield_to_maturity', raw_unit='percent')
    _, mature = select(s.country_risk, p.max_country_age_days, subject_kind='market_group', subject_code='mature',
                       metric_code='mature_market_erp', method_code='implied_mature', raw_unit='fraction')
    spread = 0.0
    if p.risk_free_method == 'subtract_cn_default_spread':
        _, spread = select(s.country_risk, p.max_country_age_days, subject_code='CN', subject_kind='country',
                           metric_code='sovereign_default_spread', method_code='rating', raw_unit='fraction')
    beta = 0.0
    industry_de = 0.0
    for industry in p.industries:
        row, value = select(s.industry_stats, p.max_beta_age_days, industry=industry.industry, industry_name=industry.industry,
                            taxonomy_code='damodaran_industry_2026', sample_region='global', metric_code=p.beta_metric,
                            statistic_code='provider_estimate', raw_unit='dimensionless')
        method = row['method_code']
        if (not isinstance(method, str) or not re.fullmatch(r'provider_marginal_tax_(?:0\.\d{12}|1\.0{12})', method)
                or type(row['sample_count']) is not int or row['sample_count'] < p.minimum_sample_count):
            raise ValueError('unsupported beta method/insufficient sample')
        beta += industry.weight * value
        if p.capital_structure_basis == 'industry_reference_weights':
            capital_row, de = select(s.industry_stats, p.max_beta_age_days, industry=industry.industry,
                industry_name=industry.industry, taxonomy_code='damodaran_industry_2026', sample_region='global',
                metric_code='debt_equity_ratio', statistic_code='provider_estimate', raw_unit='fraction',
                method_code='provider_reported')
            if de < 0 or type(capital_row['sample_count']) is not int or capital_row['sample_count'] != row['sample_count']:
                raise ValueError('invalid industry debt/equity ratio or inconsistent sample')
            industry_de += industry.weight * de
    country = 0.0
    for exposure in p.countries:
        _, value = select(s.country_risk, p.max_country_age_days, subject_code=exposure.country, subject_kind='country',
                          metric_code='country_risk_premium', method_code='rating', raw_unit='fraction')
        country += exposure.weight * exposure.exposure_scale * value
    debt_audit = None
    kd = p.debt_cost_pretax
    if p.synthetic_debt is not None:
        sp = p.synthetic_debt
        # 茅台酒类分子与合并利息分母尚不具备同范围证据，不能套用。
        if p.scope != 'consolidated' or ebit is None or interest is None or interest <= 0:
            raise ValueError('synthetic debt requires approved consolidated EBIT and positive gross interest')
        coverage = Decimal(str(ebit)) / Decimal(str(interest))
        matches = [r for r in s.credit_spreads if Decimal(r['coverage_lower']) < coverage <= Decimal(r['coverage_upper'])]
        if len(matches) != 1:
            raise ValueError('coverage outside source bands or in source gap')
        band, credit = select(s.credit_spreads, sp.max_credit_age_days, observation_id=matches[0]['observation_id'])
        sovereign = 0.0
        if sp.sovereign_spread_policy == 'add_cn_default_spread':
            _, sovereign = select(s.country_risk, p.max_country_age_days, subject_code='CN', subject_kind='country',
                metric_code='sovereign_default_spread', method_code='rating', raw_unit='fraction')
        kd = government - spread + credit + sovereign
        debt_audit = dict(status='synthetic_estimate_not_observed_rating', ebit_million_cny=ebit,
            gross_interest_million_cny=interest, coverage_ratio=str(coverage), rating=band['rating'],
            corporate_default_spread=credit, sovereign_spread_added=sovereign, debt_cost_pretax=kd,
            financial_basis='same TTM policy operating EBIT and TDX interest_expense gross interest; see consumed_inputs')
    if p.credit_band_debt is not None:
        cp=p.credit_band_debt
        band,credit=select(s.credit_spreads,cp.max_credit_age_days,rating=cp.rating)
        sovereign=0.0
        if cp.sovereign_spread_policy=='add_cn_default_spread':
            _,sovereign=select(s.country_risk,p.max_country_age_days,subject_code='CN',subject_kind='country',metric_code='sovereign_default_spread',method_code='rating',raw_unit='fraction')
        kd=government-spread+credit+sovereign
        debt_audit=dict(status='analyst_credit_band_proxy_not_observed_rating',rating=band['rating'],corporate_default_spread=credit,sovereign_spread_added=sovereign,debt_cost_pretax=kd,selection_reason=cp.selection_reason)
    market_audit=None
    market_e=market_d=None
    weight=p.target_debt_weight
    industry_capital_audit=None
    if p.capital_structure_basis == 'industry_reference_weights':
        weight=industry_de/(1+industry_de)
        industry_capital_audit=dict(status='industry_target_proxy_not_company_market_structure',
            weighted_debt_equity_ratio=industry_de, debt_weight=weight,
            method='sum(policy industry weight * source D/E), then D/(D+E)=D/E/(1+D/E)')
    if p.market is not None:
        if binding.market_capital is None or debt is None or debt<0 or bridge is None:
            raise ValueError('market WACC requires standard capital evidence and reviewed debt/scope bridge')
        if not 0<=(binding.market_capital.market_date-period).days<=p.market.max_financial_age_days:
            raise ValueError('stale/future financial debt and scope bridge')
        common,market_audit,packet=equity_market_value(binding.market_capital,p.market,code,information_as_of)
        # 逆向已审核股权桥接，分离非经营资产、金融子公司及其他索偿；不把存款当酒业债务。
        debt_key='debt_claim_proxy' if code=='300866' else 'lease_debt'
        if debt_key not in bridge.components: raise ValueError('missing debt scope bridge')
        offset=sum(Decimal(str(v)) for k,v in bridge.components.items() if k!=debt_key)
        funding=market_audit.get('funding_cash_scenario')
        if funding is not None:
            if any(date.fromisoformat(r['listing_date'])<=period for r in binding.market_capital.funding_events):
                raise ValueError('proceeds already inside financial reporting period; would double count')
            if note is None: raise ValueError('financing fee disclosure required')
            funding.update(financial_date_ipo_fee_asset_million_cny=note('ipo_prepaid'),
                financial_date_ipo_payable_million_cny=note('ipo_payable'),
                fee_overlap_status='unresolved_no_fee_reclassification_sensitivity_only')
            funding['boundaries'].append('June fee assets/payables retained; overlap with estimated net proceeds unresolved, not an accounting cash rollforward')
            offset+=Decimal(funding['cash_adjustment_million_cny'])
        operating=(common-offset)/Decimal(str(bridge.operating_ownership))
        if operating<=0: raise ValueError('nonpositive residual operating equity')
        market_e,market_d=float(operating),debt
        debt_value_audit=None
        if p.market.debt_value_basis=='contractual_cashflow_pv_upper_bound':
            if code!='300866' or note is None:
                raise ValueError('contractual debt maturity evidence supported only for reviewed Anker scope')
            market_d,debt_value_audit=contractual_debt_value(note,debt,kd)
        weight=market_d/(market_e+market_d)
        market_audit.update(operating_equity_million_cny=str(operating),debt_million_cny=market_d,debt_valuation=debt_value_audit,
            debt_value_basis=p.market.debt_value_basis,financial_age_days=(binding.market_capital.market_date-period).days,nondebt_bridge_offset_million_cny=str(offset),
            operating_ownership=bridge.operating_ownership,source_snapshot=packet,
            current_fair_value_complete=False,
            unresolved_inputs=(['post_report_net_cash_and_borrowing_movements','convertible_option_market_value','operating_country_risk_exposure'] if code=='300866' else ['liquor_finance_subsidiary_capital_allocation','company_specific_credit_spread','post_report_nonoperating_asset_values']),
            boundaries=['debt uses the explicit selected valuation basis; non-operating claims remain financial-date proxies',
            'carried shares may miss changes after latest disclosure; age policy is explicit',
            'market WACC does not update the historical DCF share/cash bridge or make its per-share value a current target'])
    elif binding.market_capital is not None:
        raise ValueError('market packet supplied without market policy')
    components = ReferenceCapitalInputs(risk_free_rate=government-spread, beta_u=beta, mature_market_erp=mature,
        country_risk_contribution=country, debt_weight=weight, capital_structure_basis=p.capital_structure_basis,
        market_equity=market_e,estimated_debt=market_d, tax_shield_rate=p.tax_shield_rate,
        debt_cost_pretax=kd, debt_cost_basis=("analyst_credit_reference" if p.credit_band_debt else "synthetic_reference" if debt_audit else "explicit_policy"))
    result = compute_reference_cost_of_capital(components)
    return components, dict(policy=p.model_dump(mode='json'), selected_observations=used,
        market_capital=market_audit, industry_capital=industry_capital_audit, synthetic_debt=debt_audit, government_yield=government, sovereign_default_spread_adjustment=spread,
        result=result.model_dump(mode='json'), boundaries=[('industry reference target proxy, not company market capital structure' if industry_capital_audit is not None else 'explicit target weights, not observed market capital structure' if market_audit is None else 'market equity with estimated debt and operating scope adjustments'),
        'industry and country weights are analyst policy', 'constant WACC including terminal period',
        'reference packet provenance is validated structurally; not a cryptographic signature of the database'])
