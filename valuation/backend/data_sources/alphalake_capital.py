"""行业历史资本效率参考的显式采用政策；不改写公司财务事实。"""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import math
import re
from typing import Literal

from pydantic import Field, model_validator
from data_sources.alphalake_wacc import Strict


class CapitalReferences(Strict):
    contract_version: Literal['alphalake-industry-capital-v1']
    information_as_of: datetime
    recorded_cutoff: datetime | None
    release: dict
    observations: list[dict]

    @model_validator(mode='after')
    def validate_evidence(self):
        try:
            self._check()
        except (KeyError, TypeError, InvalidOperation) as error:
            raise ValueError('malformed capital references: '+str(error)) from error
        return self

    def _check(self):
        r=self.release
        if self.information_as_of.utcoffset() is None or (self.recorded_cutoff is not None and self.recorded_cutoff.utcoffset() is None):
            raise ValueError('aware capital reference cutoffs required')
        if (r['source'],r['dataset'],r['parser_version'],r['source_url'],r['availability_basis']) != (
            'damodaran','global-industry-capital-2026-v1','damodaran-global-capital-v1',
            'https://pages.stern.nyu.edu/~adamodar/pc/datasets/capexGlobal.xls','first_seen'):
            raise ValueError('unsupported capital reference source')
        for key in ('release_id','artifact_id'):
            if type(r[key]) is not int or r[key]<=0:raise ValueError('invalid capital reference identity')
        for key in ('artifact_sha256','content_key'):
            if not re.fullmatch('[0-9a-f]{64}',r[key]):raise ValueError('invalid capital reference hash')
        parts=r['normalization_version'].split(';',2)
        if len(parts)!=3 or parts[0]!='global-capital-decimal12-v1' or not re.fullmatch('[0-9a-f]{64}',parts[1]) or not parts[2]:
            raise ValueError('unsupported capital normalization')
        if hashlib.sha256((r['artifact_sha256']+'\n'+r['parser_version']+'\n'+r['normalization_version']).encode()).hexdigest()!=r['content_key']:
            raise ValueError('capital release digest mismatch')
        available,recorded=map(datetime.fromisoformat,(r['available_at'],r['recorded_at']))
        if available.utcoffset() is None or recorded.utcoffset() is None or available>self.information_as_of or recorded<available or (self.recorded_cutoff is not None and recorded>self.recorded_cutoff):
            raise ValueError('capital reference unavailable at cutoff')
        observed=date.fromisoformat(r['source_version'])
        if observed.year!=2026 or observed>self.information_as_of.date():raise ValueError('unsupported capital observation date')
        seen,ids,locators=set(),set(),set()
        if len(self.observations)!=94:raise ValueError('incomplete capital reference scope')
        for o in self.observations:
            if (o['release_id'],o['artifact_id'],o['observation_date'])!=(r['release_id'],r['artifact_id'],r['source_version']):
                raise ValueError('capital observation provenance mismatch')
            if (o['metric_code'],o['method_code'],o['sample_region'],o['statistic_code'],o['value_status'],o['raw_unit'])!=(
                'sales_to_invested_capital_ltm','source_sales_to_invested_capital_ltm','global','provider_estimate','reported','dimensionless'):
                raise ValueError('capital observation semantics mismatch')
            locator=re.fullmatch(r'Industry Averages!J([0-9]+)',o['source_locator'])
            if not locator or not 9<=int(locator[1])<=102 or o['source_locator'] in locators:
                raise ValueError('invalid capital cell location')
            if type(o['observation_id']) is not int or o['observation_id']<=0 or o['observation_id'] in ids or not isinstance(o['industry'],str) or not o['industry'].strip() or o['industry'] in seen or type(o['sample_count']) is not int or o['sample_count']<=0:
                raise ValueError('invalid capital industry/sample identity')
            if not isinstance(o['value'],str) or not re.fullmatch(r'[0-9]+\.[0-9]{12}',o['value']):raise ValueError('canonical capital decimal required')
            v,raw=Decimal(o['value']),Decimal(o['raw_value'])
            if not raw.is_finite() or v<=0 or abs(v-raw)>Decimal('0.0000000000005'):raise ValueError('capital source conversion mismatch')
            seen.add(o['industry']);ids.add(o['observation_id']);locators.add(o['source_locator'])


class CapitalPolicy(Strict):
    code: str = Field(pattern=r'^[0-9]{6}$')
    report_period: date
    industry: str = Field(min_length=1)
    mapping_reason: str = Field(min_length=1)
    minimum_sample_count: int = Field(gt=0)
    max_age_days: int = Field(ge=0)
    ratio_multiplier: float = Field(gt=0)
    adoption_basis: Literal['historical_industry_ratio_as_marginal_reinvestment_proxy']
    adoption_reason: str = Field(min_length=1)


class CapitalBinding(Strict):
    references: CapitalReferences
    policy: CapitalPolicy


def resolve_capital(binding,code,period,asof):
    p,r=binding.policy,binding.references
    if p.code!=code or p.report_period!=period or r.information_as_of>asof:
        raise ValueError('capital binding differs from security/period/cutoff')
    matches=[o for o in r.observations if o['industry']==p.industry]
    if len(matches)!=1:raise ValueError('missing or ambiguous selected capital industry')
    observation=matches[0]
    if observation['sample_count']<p.minimum_sample_count or (asof.date()-date.fromisoformat(observation['observation_date'])).days>p.max_age_days:
        raise ValueError('capital industry sample too small or reference stale')
    value=float(observation['value'])*p.ratio_multiplier
    if not math.isfinite(value) or value<=0:raise ValueError('invalid adopted capital ratio')
    return value,dict(release=r.release,observation=observation,policy=p.model_dump(mode='json'),sales_to_capital=value,
        boundaries=['industry historical ratio adopted as marginal reinvestment proxy by explicit policy; not a company reported fact',
                    'source methodology capitalizes R&D and operating leases; generic company accounting scope is not automatically reconciled'])
