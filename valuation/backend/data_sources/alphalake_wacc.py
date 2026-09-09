"""固定发布版本的 WACC 输入选择；事实、选择理由和目标资本结构分开保存。"""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from engine.data_dictionary import ReferenceCapitalInputs
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


class WACCPolicy(Strict):
    policy_id: str = Field(min_length=1)
    code: Literal['300866', '600519']
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
    capital_structure_basis: Literal['target_weights']
    target_debt_weight: float = Field(ge=0, lt=1)
    capital_structure_reason: str = Field(min_length=1)
    debt_cost_pretax: float = Field(ge=0, lt=1)
    debt_cost_reason: str = Field(min_length=1)
    tax_shield_rate: float = Field(ge=0, le=1)
    tax_shield_reason: str = Field(min_length=1)
    stable_wacc_policy: Literal['hold_current']
    max_yield_age_days: int = Field(ge=0)
    max_beta_age_days: int = Field(ge=0)
    max_country_age_days: int = Field(ge=0)

    @model_validator(mode='after')
    def weights(self):
        for rows, key in [(self.industries, 'industry'), (self.countries, 'country')]:
            if len({getattr(r, key) for r in rows}) != len(rows) or abs(sum(r.weight for r in rows)-1) > 1e-9:
                raise ValueError('unique explicit weights summing to one required')
        return self


class ReferenceSnapshot(Strict):
    contract_version: Literal['alphalake-wacc-references-v1']
    information_as_of: datetime
    recorded_cutoff: datetime | None
    releases: list[dict]
    country_risk: list[dict]
    industry_stats: list[dict]
    yield_curve: list[dict]

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
        if len(releases) != 3 or len(self.releases) != 3:
            raise ValueError('three distinct reference releases required')
        expected = [('country_risk', 'damodaran', 'country-risk-cn-hk-us-rating-v1', 10),
                    ('industry_stats', 'damodaran', 'global-industry-beta-2026-v1', 376),
                    ('yield_curve', 'chinabond', 'cny-government-eight-tenors-v1', 8)]
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
                if not raw.is_finite() or abs(raw/(100 if field == 'yield_curve' else 1)-value) > Decimal('0.0000000000005'):
                    raise ValueError('reference raw/standard mismatch')
                if field != 'yield_curve' and r['value_status'] != 'reported':
                    raise ValueError('missing reference value')
        return self


class WACCBinding(Strict):
    references: ReferenceSnapshot
    policy: WACCPolicy


def resolve_wacc(binding: WACCBinding, code: str, period: date, information_as_of: datetime):
    s, p = binding.references, binding.policy
    expected_scope = 'consolidated' if code == '300866' else 'liquor_proxy'
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
    for industry in p.industries:
        row, value = select(s.industry_stats, p.max_beta_age_days, industry=industry.industry, industry_name=industry.industry,
                            taxonomy_code='damodaran_industry_2026', sample_region='global', metric_code=p.beta_metric,
                            statistic_code='provider_estimate', raw_unit='dimensionless')
        method = row['method_code']
        if (not isinstance(method, str) or not re.fullmatch(r'provider_marginal_tax_(?:0\.\d{12}|1\.0{12})', method)
                or type(row['sample_count']) is not int or row['sample_count'] < p.minimum_sample_count):
            raise ValueError('unsupported beta method/insufficient sample')
        beta += industry.weight * value
    country = 0.0
    for exposure in p.countries:
        _, value = select(s.country_risk, p.max_country_age_days, subject_code=exposure.country, subject_kind='country',
                          metric_code='country_risk_premium', method_code='rating', raw_unit='fraction')
        country += exposure.weight * exposure.exposure_scale * value
    components = ReferenceCapitalInputs(risk_free_rate=government-spread, beta_u=beta, mature_market_erp=mature,
        country_risk_contribution=country, debt_weight=p.target_debt_weight, tax_shield_rate=p.tax_shield_rate,
        debt_cost_pretax=p.debt_cost_pretax)
    result = compute_reference_cost_of_capital(components)
    return components, dict(policy=p.model_dump(mode='json'), selected_observations=used,
        government_yield=government, sovereign_default_spread_adjustment=spread,
        result=result.model_dump(mode='json'), boundaries=['explicit target weights, not observed market capital structure',
        'industry and country weights are analyst policy', 'constant WACC including terminal period',
        'reference packet provenance is validated structurally; not a cryptographic signature of the database'])
