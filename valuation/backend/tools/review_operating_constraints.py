"""只读核对经营证据与显式预测约束；不选择参数、不修改估值输入。"""
import argparse
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
import json
from pathlib import Path
import re
import sqlite3
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from data_sources.paths import workspace_path
from tools.export_alphalake_sqlite import digest
from tools.narrative_valuation import Evidence, read_json, source_path
from tools.pdf_evidence import verified_pdf_text
from tools.review_native_policy import review_report

UNITS = {
    'CNY': ('CNY', '1'), 'thousand_CNY': ('CNY', '1000'),
    'ten_thousand_CNY': ('CNY', '10000'), 'hundred_million_CNY': ('CNY', '100000000'),
    'percent': ('ratio', '.01'), 'fraction': ('ratio', '1'),
    'units': ('units', '1'), 'GW': ('GW', '1'), 'GWh': ('GWh', '1'),
}


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, str_strip_whitespace=True)


def number(text):
    value = Decimal(text.replace(',', '').replace('−', '-'))
    if not value.is_finite():
        raise ValueError('finite printed amount required')
    return value


class Observation(Record):
    id: str
    ticker: str = Field(pattern=r'^(SHSE|SZSE):[0-9]{6}$')
    metric: Literal['revenue_cumulative', 'sales_volume', 'production', 'gross_margin',
                    'revenue_growth', 'capacity_utilization', 'market_share', 'capital_commitment',
                    'research_investment', 'research_intensity']
    segment: str
    scope: str
    period_start: date
    period_end: date
    kind: Literal['reported', 'guidance', 'plan']
    printed_low: str
    printed_high: str | None = None
    comparison: Literal['value', 'greater_than'] = 'value'
    source_unit: str
    source_id: str
    physical_page: int = Field(ge=1)
    anchors: list[str] = Field(min_length=1)

    @model_validator(mode='after')
    def dimensions(self):
        if self.source_unit not in UNITS or self.period_start > self.period_end:
            raise ValueError('known unit and ordered period required')
        unit, _ = UNITS[self.source_unit]
        expected = ('CNY' if self.metric in ('revenue_cumulative', 'capital_commitment', 'research_investment') else
                    'volume' if self.metric in ('sales_volume', 'production') else 'ratio')
        accepted = ('units', 'GW', 'GWh') if expected == 'volume' else (expected,)
        if unit not in accepted:
            raise ValueError('metric/unit mismatch')
        if self.bounds[0] > self.bounds[1] or not self.segment or not self.scope:
            raise ValueError('ordered interval and explicit business scope required')
        if self.comparison != 'value' and self.printed_high is not None:
            raise ValueError('threshold is not a closed reported interval')
        if any(not re.sub(r'\s+', '', a) for a in self.anchors):
            raise ValueError('nonempty evidence anchors required')
        return self

    @property
    def bounds(self):
        scale = Decimal(UNITS[self.source_unit][1])
        high = self.printed_low if self.printed_high is None else self.printed_high
        return (number(self.printed_low)*scale, number(high)*scale)


class Calculation(Record):
    id: str
    ticker: str
    kind: Literal['growth', 'unit_revenue', 'annual_remainder', 'forward_ttm', 'revenue_mix']
    observations: list[str]
    annual_revenue_condition_cny: Decimal | None = None
    next_h1_growth_condition: Decimal | None = None
    total_growth_condition: Decimal | None = None
    growth_driver: str | None = None
    other_segments_growth_condition: Decimal | None = None
    condition: str = Field(min_length=1)


class Card(Record):
    ticker: str
    parameter: Literal['growth', 'margin', 'capital']
    horizon: Literal['first_year', 'whole_path']
    support: Literal['quantitative_conditional', 'directional', 'not_established']
    observations: list[str]
    calculations: list[str]
    interpretation: str = Field(min_length=1)
    missing: str = Field(min_length=1)


class Binding(Record):
    ticker: str = Field(pattern=r'^(SHSE|SZSE):[0-9]{6}$')
    report_source: str


class Manifest(Record):
    contract: Literal['operating-constraints-input-v1']
    sqlite_sha256: str
    scope: list[str] = Field(min_length=1)
    sources: list[Evidence]
    observations: list[Observation]
    calculations: list[Calculation]
    bindings: list[Binding]
    cards: list[Card]


def calculate(rule, observations):
    if set(rule.observations)-set(observations):
        raise ValueError('unknown calculation observation')
    args = [observations[key] for key in rule.observations]
    if not args or any(o.ticker != rule.ticker for o in args):
        raise ValueError('calculation security mismatch')
    if any(o.comparison != 'value' for o in args):
        raise ValueError('a threshold statement is not an exact calculation input; supply an explicit condition')
    if rule.kind == 'revenue_mix':
        return revenue_mix(rule, args)
    if any(v is not None for v in (rule.total_growth_condition, rule.growth_driver, rule.other_segments_growth_condition)):
        raise ValueError('business growth conditions require a revenue mix')
    first = args[0]
    unit = UNITS[first.source_unit][0]
    if len({(UNITS[o.source_unit][0], o.scope, o.segment) for o in args}) != 1 and rule.kind != 'unit_revenue':
        raise ValueError('calculation unit/business scope mismatch')
    annual, growth = rule.annual_revenue_condition_cny, rule.next_h1_growth_condition
    if rule.kind in ('growth', 'unit_revenue') and (annual is not None or growth is not None):
        raise ValueError('unused forecast conditions are not allowed')
    if rule.kind in ('annual_remainder', 'forward_ttm'):
        if (len(args) != 1 or first.metric != 'revenue_cumulative' or unit != 'CNY'
                or annual is None or not annual.is_finite() or annual < 0
                or first.period_start != date(first.period_end.year, 1, 1)):
            raise ValueError('explicit annual revenue and compatible YTD required')
        if rule.kind == 'annual_remainder':
            if (first.period_end.month not in (3, 6, 9)
                    or (first.period_end+timedelta(days=1)).day != 1 or growth is not None):
                raise ValueError('partial calendar-year cumulative period required')
            low, high = annual-first.bounds[1], annual-first.bounds[0]
            start, end = first.period_end+timedelta(days=1), date(first.period_end.year, 12, 31)
        else:
            if (first.period_end != date(first.period_end.year, 6, 30) or first.kind != 'reported'
                    or growth is None or not growth.is_finite() or growth < -1):
                raise ValueError('reported H1 and explicit next-H1 growth required')
            scaled = sorted(growth*v for v in first.bounds)
            low, high = annual+scaled[0], annual+scaled[1]
            start, end = date(first.period_end.year, 7, 1), date(first.period_end.year+1, 6, 30)
        meaning = 'explicit_conditions_not_reported_forecast'
    else:
        if len(args) != 2:
            raise ValueError('two compatible observations required')
        other = args[1]
        if rule.kind == 'growth':
            if (first.metric != other.metric or first.segment != other.segment
                    or first.period_start.replace(year=first.period_start.year-1) != other.period_start
                    or first.period_end.replace(year=first.period_end.year-1) != other.period_end):
                raise ValueError('growth requires matching year-on-year windows and metrics')
            unit, meaning = 'ratio', 'observed_growth_not_future_growth'
        else:
            if (first.metric != 'revenue_cumulative' or other.metric != 'sales_volume'
                    or unit != 'CNY' or (first.scope, first.segment, first.period_start, first.period_end)
                    != (other.scope, other.segment, other.period_start, other.period_end)):
                raise ValueError('unit revenue requires sales volume, same period and business; production is not sales')
            unit, meaning = 'CNY/'+UNITS[other.source_unit][0], 'mixed_unit_revenue_not_like_for_like_price'
        if other.bounds[0] <= 0:
            return dict(id=rule.id, status='not_computable_nonpositive_denominator', future_bound=None)
        quotients = [x/y for x in first.bounds for y in other.bounds]
        adjustment = 1 if rule.kind == 'growth' else 0
        low, high = min(quotients)-adjustment, max(quotients)-adjustment
        start, end = first.period_start, first.period_end
    return dict(id=rule.id, status='calculated', low=str(low), high=str(high), unit=unit,
                period_start=start.isoformat(), period_end=end.isoformat(), meaning=meaning,
                condition=rule.condition, future_bound=None)


def revenue_mix(rule, args):
    """原文同比按显示精度反推区间，不制造精确上期分部收入。"""
    if (len(args) < 4 or len(args)%2 or len({a.id for a in args}) != len(args)
            or rule.annual_revenue_condition_cny is not None or rule.next_h1_growth_condition is not None):
        raise ValueError('total pair and unique segment pairs required')
    current, prior = args[:2]
    def same_period(a,b):
        return (a.period_start,a.period_end)==(b.period_start,b.period_end)
    def yoy(a,b):
        return (a.period_start.replace(year=a.period_start.year-1),a.period_end.replace(year=a.period_end.year-1)) == (b.period_start,b.period_end)
    def amount(a):
        return a.metric=='revenue_cumulative' and UNITS[a.source_unit][0]=='CNY' and a.bounds[0]==a.bounds[1]
    if (not amount(current) or not amount(prior) or current.segment != prior.segment or not yoy(current,prior)
            or current.bounds[0] <= 0 or prior.bounds[0] <= 0
            or any(a.ticker != current.ticker or a.scope != current.scope or a.kind != 'reported' for a in args)):
        raise ValueError('positive comparable reported company totals required')
    total, base = current.bounds[0], prior.bounds[0]
    rows, axes, seen = [], set(), set()
    display_bound = number_quantum(current)/2
    prior_display_bound = number_quantum(prior)/2
    for now, before in zip(args[2::2],args[3::2],strict=True):
        parts = now.segment.split('/')
        if (len(parts)!=2 or not all(parts) or now.segment in seen or now.segment==current.segment
                or now.segment != before.segment or not amount(now) or not same_period(now,current)):
            raise ValueError('unique explicit partition/segment and matching current revenue required')
        axes.add(parts[0]);seen.add(now.segment)
        if amount(before) and yoy(now,before):
            low,high=before.bounds
            basis='reported_prior_amount'
            prior_display_bound+=number_quantum(before)/2
        elif (before.metric=='revenue_growth' and before.source_unit in ('percent','fraction')
                and same_period(now,before) and before.bounds[0]==before.bounds[1]):
            rate=before.bounds[0];half=number_quantum(before)/2
            if now.bounds[0]<=0 or 1+rate-half<=0:
                raise ValueError('rounded growth cannot reconstruct a nonpositive or absent base')
            low,high=now.bounds[0]/(1+rate+half),now.bounds[0]/(1+rate-half)
            basis='interval_from_rounded_growth_not_reported_amount'
        else:
            raise ValueError('compatible prior revenue or rounded same-period growth required')
        display_bound+=number_quantum(now)/2
        rows.append(dict(segment=now.segment,current_revenue_cny=str(now.bounds[0]),
            prior_revenue_interval_cny=[str(low),str(high)],prior_basis=basis,
            rounding_policy='nearest_display_unit_assumed' if before.metric=='revenue_growth' else 'amount_as_printed',
            current_revenue_share=str(now.bounds[0]/total),
            contribution_to_total_growth_interval=[str((now.bounds[0]-high)/base),str((now.bounds[0]-low)/base)]))
    if len(axes)!=1:
        raise ValueError('product, geography and channel partitions cannot be added together')
    covered=sum((Decimal(r['current_revenue_cny']) for r in rows),Decimal(0))
    residual=total-covered
    if residual < -display_bound:
        raise ValueError('segments exceed company revenue; overlap or wrong scope')
    prior_low=sum((Decimal(r['prior_revenue_interval_cny'][0]) for r in rows),Decimal(0))
    prior_high=sum((Decimal(r['prior_revenue_interval_cny'][1]) for r in rows),Decimal(0))
    prior_residual=[base-prior_high,base-prior_low]
    if prior_residual[1] < -prior_display_bound:
        raise ValueError('reconstructed prior segments exceed company revenue')
    complete=abs(residual)<=display_bound
    if complete and not (prior_residual[0]-prior_display_bound<=0<=prior_residual[1]+prior_display_bound):
        raise ValueError('complete partition does not reconcile prior total within displayed rounding')
    condition_values=(rule.total_growth_condition,rule.growth_driver,rule.other_segments_growth_condition)
    requirement=None
    if any(v is not None for v in condition_values):
        target,driver,other=condition_values
        if any(v is None for v in condition_values) or target < -1 or other < -1 or driver not in seen:
            raise ValueError('explicit total, other-business growth and existing driver required')
        if not complete:
            requirement=dict(status='unclassified_revenue_requires_explicit_condition',value=None)
        else:
            value=Decimal(next(r['current_revenue_cny'] for r in rows if r['segment']==driver))
            if value<=0:
                raise ValueError('positive driver revenue required')
            # 仅作条件负担反推；不将上期反推区间或余数归入某项业务。
            required=(total*target-(total-value)*other)/value
            requirement=dict(status='conditional_identity_not_forecast',driver=driver,
                total_growth=str(target),other_segments_growth=str(other),required_driver_growth=str(required),
                feasible_nonnegative_driver_revenue=required>=-1,
                period_start=current.period_start.replace(year=current.period_start.year+1).isoformat(),
                period_end=current.period_end.replace(year=current.period_end.year+1).isoformat(),
                boundary='仅下一年同窗条件；半年分部不充当TTM分部，不认证公司增长')
    return dict(id=rule.id,status='calculated',meaning='reported_revenue_mix_not_future_growth',unit='CNY',
        period_start=current.period_start.isoformat(),period_end=current.period_end.isoformat(),
        total_current_cny=str(total),total_prior_cny=str(base),observed_total_growth=str(total/base-1),
        partition=next(iter(axes)),segments=rows,current_unclassified_residual_cny=str(residual),
        current_sum_rounding_bound_cny=str(display_bound),
        coverage='complete_with_display_rounding' if complete else 'partial_not_zero_filled',
        prior_total_minus_reconstruction_interval_cny=list(map(str,prior_residual)),
        requirement=requirement,condition=rule.condition,future_bound=None,
        boundary='反推区间只反映原文同比显示精度，不是统计区间；各端点不保证同时可达；项目互斥性仍须人工语义核对')


def number_quantum(observation):
    return Decimal(1).scaleb(number(observation.printed_low).as_tuple().exponent)*Decimal(UNITS[observation.source_unit][1])


def review(manifest, database):
    spec = Manifest.model_validate(manifest)
    if digest(database) != spec.sqlite_sha256:
        raise ValueError('SQLite snapshot differs')
    scope = set(spec.scope)
    if len(scope) != len(spec.scope) or {b.ticker for b in spec.bindings} != scope or len(spec.bindings) != len(scope):
        raise ValueError('retain complete unique company denominator')
    sources = {s.id: s for s in spec.sources}
    observations = {o.id: o for o in spec.observations}
    calculations = {c.id: c for c in spec.calculations}
    if any(len(v) != len(items) for v, items in ((sources,spec.sources),(observations,spec.observations),(calculations,spec.calculations))):
        raise ValueError('duplicate evidence/calculation IDs')
    with sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True) as conn:
        metadata = dict(conn.execute("SELECT key,value FROM metadata WHERE key IN ('report_period','information_as_of','reference_snapshot_id')"))
    paths = {key:source_path(source) for key,source in sources.items()}
    pdf_pages = {}
    for observation in observations.values():
        if observation.ticker not in scope:
            raise ValueError('observation outside scope')
        if observation.source_id not in sources:
            raise ValueError('unknown observation source')
        source = sources[observation.source_id]
        if paths[source.id].suffix.lower() != '.pdf':
            raise ValueError('operating observations require original PDF evidence')
        if source.id not in pdf_pages:
            pdf_pages[source.id] = verified_pdf_text(paths[source.id], source.sha256).split('\f')
        pages = pdf_pages[source.id]
        if observation.physical_page > len(pages) or not pages[observation.physical_page-1].strip():
            raise ValueError('source physical page is unavailable')
        text = re.sub(r'\s+', '', pages[observation.physical_page-1])
        anchors = observation.anchors+[observation.printed_low]
        if observation.printed_high is not None:
            anchors.append(observation.printed_high)
        if any(re.sub(r'\s+', '', anchor) not in text for anchor in anchors):
            raise ValueError('source page/printed value/anchor differs: '+observation.id)
    computed = {key:calculate(rule, observations) for key,rule in calculations.items()}
    if any(c.ticker not in scope for c in calculations.values()):
        raise ValueError('calculation outside scope')
    expected = {(t,p) for t in scope for p in ('growth','margin','capital')}
    if len(spec.cards) != len(expected) or {(c.ticker,c.parameter) for c in spec.cards} != expected:
        raise ValueError('retain growth, margin and capital cards for every company')
    reports, report_windows = {}, {}
    for binding in spec.bindings:
        if binding.report_source not in sources or sources[binding.report_source].kind != 'snapshot_baseline':
            raise ValueError('native report source binding required')
        body = read_json(paths[binding.report_source])
        inputs = body['inputs']
        prepared = inputs.get('prepared_ttm')
        if (inputs['ticker'] != binding.ticker or body.get('reference_snapshot',{}).get('id') != metadata['reference_snapshot_id']
                or max(filter(None,(inputs.get('period_date_10k'),inputs.get('period_date_10q'))))[:10] != metadata['report_period']
                or (prepared is not None and prepared.get('information_as_of') != metadata['information_as_of'])):
            raise ValueError('native report identity/reference/information cutoff differs')
        reports[binding.ticker] = review_report(body)
        end = date.fromisoformat(metadata['report_period'])
        report_windows[binding.ticker] = (dict(period_start=prepared['period_start'],period_end=prepared['period_end'])
            if prepared is not None else dict(period_start=date(end.year,1,1).isoformat(),period_end=end.isoformat()))
    cards = []
    for card in spec.cards:
        if set(card.observations)-set(observations) or set(card.calculations)-set(calculations):
            raise ValueError('unknown card evidence')
        if any(observations[k].ticker != card.ticker for k in card.observations) or any(calculations[k].ticker != card.ticker for k in card.calculations):
            raise ValueError('card evidence security mismatch')
        if card.support == 'directional' and not card.observations:
            raise ValueError('directional assessment requires operating observations')
        if card.support == 'quantitative_conditional' and (card.parameter != 'growth' or card.horizon != 'first_year'
                or not any(calculations[k].kind == 'forward_ttm' and computed[k]['status'] == 'calculated' for k in card.calculations)):
            raise ValueError('quantitative support requires an explicit forward-TTM bridge; gross margin is not EBIT margin')
        reviewed = reports[card.ticker]
        values = [r[{'growth':'growth','margin':'adjusted_operating_margin','capital':'reinvestment_million_cny'}[card.parameter]] for r in reviewed['forecast']]
        relations = [dict(computed[k]) for k in card.calculations]
        for relation in relations:
            if calculations[relation['id']].kind == 'revenue_mix':
                window=report_windows[card.ticker]
                forecast={k:date.fromisoformat(v).replace(year=date.fromisoformat(v).year+1).isoformat() for k,v in window.items()}
                relation['native_alignment']=dict(base_window=window,first_forecast_window=forecast,
                    mix_matches_base=all(relation[k]==v for k,v in window.items()),
                    condition_matches_forecast=(all(relation['requirement'].get(k)==v for k,v in forecast.items())
                        if relation['requirement'] is not None else None),
                    native_first_year_growth=reviewed['forecast'][0]['growth'],automatic_conversion=False)
            if calculations[relation['id']].kind == 'forward_ttm' and relation['status'] == 'calculated':
                native = Decimal(str(reviewed['forecast'][0]['revenue_million_cny']))*1000000
                relation['native_first_year_revenue_cny'] = str(native)
                relation['native_minus_relation_interval_cny'] = [str(native-Decimal(relation[k])) for k in ('high','low')]
                relation['alignment'] = 'difference_disclosed_not_automatic_acceptance'
        cards.append(dict(**card.model_dump(mode='json'), actual_forecast_values=values,
            actual_value_unit='million_CNY_total_net_reinvestment' if card.parameter == 'capital' else 'ratio',
            assessment_origin='analyst_declared_not_machine_economic_certification', future_parameter_bounds=None,
            quantitative_relations=relations, automatic_adoption=False))
    if digest(database) != spec.sqlite_sha256:
        raise ValueError('SQLite changed during review')
    return dict(contract='operating-constraints-review-v1', scope=spec.scope, metadata=metadata,
        observations=[dict(**o.model_dump(mode='json'), normalized_low=str(o.bounds[0]),
                           normalized_high=None if o.comparison == 'greater_than' else str(o.bounds[1]),
                           normalized_unit=UNITS[o.source_unit][0]) for o in observations.values()],
        calculations=list(computed.values()), cards=cards, summary=dict(companies=len(scope), cards=len(cards),
        support_counts=dict(Counter(c.support for c in spec.cards))), automatic_adoption=False,
        boundary='核对来源、期间、单位与条件算术；方向判断仍属研究者，不以历史或毛利率自动限定未来调整EBIT率，不生成预测或回写事实')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or not args.output.resolve().is_relative_to(workspace_path().resolve()):
        parser.error('new output under workspace required')
    result = review(read_json(args.manifest), args.database)
    result['manifest_sha256'] = digest(args.manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps(dict(contract=result['contract'], **result['summary']), ensure_ascii=False))


if __name__ == '__main__':
    main()
