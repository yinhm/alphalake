from copy import deepcopy
from datetime import date
from decimal import Decimal
import hashlib
import json
import sqlite3

import pytest

from tools.review_operating_constraints import Calculation, Observation, calculate


def observation(identifier='revenue', **changes):
    data = dict(id=identifier, ticker='SZSE:300866', metric='revenue_cumulative', segment='battery',
                scope='consolidated', period_start='2026-01-01', period_end='2026-06-30', kind='reported',
                printed_low='120', source_unit='CNY', source_id='report', physical_page=1,
                anchors=['营业收入', '单位：元'])
    return Observation.model_validate(data | changes)


def rule(kind, ids, **changes):
    return Calculation.model_validate(dict(id='check', ticker='SZSE:300866', kind=kind,
        observations=ids, condition='同期间同业务；未来数值另由研究者明确给定') | changes)


def test_growth_preserves_historical_role_and_rejects_noncomparable_periods():
    observations = dict(current=observation(), prior=observation('prior', printed_low='100',
        period_start='2025-01-01', period_end='2025-06-30'))
    result = calculate(rule('growth', ['current','prior']), observations)
    assert Decimal(result['low']) == Decimal('.2')
    assert result['future_bound'] is None
    assert result['meaning'] == 'observed_growth_not_future_growth'
    bad = observations | dict(prior=observation('prior', printed_low='100',
        period_start='2025-01-01', period_end='2025-12-31'))
    with pytest.raises(ValueError, match='year-on-year'):
        calculate(rule('growth', ['current','prior']), bad)


def test_zero_base_is_not_zero_growth_and_negative_revenue_interval_is_preserved():
    current = observation(printed_low='-20', printed_high='-10')
    prior = observation('prior', printed_low='10', printed_high='20',
        period_start='2025-01-01', period_end='2025-06-30')
    result = calculate(rule('growth',['current','prior']), dict(current=current,prior=prior))
    assert (Decimal(result['low']),Decimal(result['high'])) == (Decimal('-3'),Decimal('-1.5'))
    zero = prior.model_copy(update={'printed_low':'0','printed_high':'0'})
    result = calculate(rule('growth',['current','prior']), dict(current=current,prior=zero))
    assert result['status'] == 'not_computable_nonpositive_denominator'
    assert 'low' not in result


def test_unit_revenue_is_mixed_realization_and_production_is_not_sales():
    revenue = observation(printed_low='100', source_unit='hundred_million_CNY')
    sales = observation('sales', metric='sales_volume', printed_low='20', source_unit='GWh')
    result = calculate(rule('unit_revenue',['revenue','sales']), dict(revenue=revenue,sales=sales))
    assert Decimal(result['low']) == Decimal('500000000')
    assert result['unit'] == 'CNY/GWh'
    assert result['meaning'] == 'mixed_unit_revenue_not_like_for_like_price'
    with pytest.raises(ValueError, match='production is not sales'):
        calculate(rule('unit_revenue',['revenue','sales']),
                  dict(revenue=revenue,sales=sales.model_copy(update={'metric':'production'})))


@pytest.mark.parametrize('field,value', [('segment','other'),('scope','parent'),('period_end',date(2026,3,31))])
def test_unit_revenue_requires_same_scope_and_window(field, value):
    revenue = observation()
    sales = observation('sales',metric='sales_volume',printed_low='10',source_unit='units')
    with pytest.raises(ValueError,match='same period and business'):
        calculate(rule('unit_revenue',['revenue','sales']),
                  dict(revenue=revenue,sales=sales.model_copy(update={field:value})))


def test_guidance_remainder_keeps_interval_and_requires_explicit_annual_condition():
    obs=observation(kind='guidance',period_end='2026-09-30',printed_low='70.8',printed_high='71.8',source_unit='hundred_million_CNY')
    r=rule('annual_remainder',['guidance'],annual_revenue_condition_cny='10000000000')
    result=calculate(r,dict(guidance=obs))
    assert Decimal(result['low']) == Decimal('2820000000')
    assert Decimal(result['high']) == Decimal('2920000000')
    assert result['period_start']=='2026-10-01'
    assert result['meaning']=='explicit_conditions_not_reported_forecast'
    with pytest.raises(ValueError,match='explicit annual'):
        calculate(r.model_copy(update={'annual_revenue_condition_cny':None}),dict(guidance=obs))


def test_forward_ttm_requires_two_explicit_judgments_and_does_not_rewrite_input():
    raw=dict(h1=observation(printed_low='4391461594.29'))
    r=rule('forward_ttm',['h1'],annual_revenue_condition_cny='10000000000',next_h1_growth_condition='.2')
    before=deepcopy(raw)
    result=calculate(r,raw)
    assert Decimal(result['low']) == Decimal('10878292318.858')
    assert result['period_end']=='2027-06-30'
    assert result['future_bound'] is None and raw==before
    with pytest.raises(ValueError,match='reported H1'):
        calculate(r.model_copy(update={'next_h1_growth_condition':None}),raw)


def test_management_threshold_cannot_be_used_as_an_exact_revenue_observation():
    threshold=observation(printed_low='100',source_unit='hundred_million_CNY',kind='plan',comparison='greater_than')
    with pytest.raises(ValueError,match='threshold statement'):
        calculate(rule('forward_ttm',['h1'],annual_revenue_condition_cny='10000000000',next_h1_growth_condition='.2'),dict(h1=threshold))


@pytest.mark.parametrize('change', [dict(printed_low='NaN'),dict(printed_high=''),dict(printed_high='0'),
    dict(source_unit='GW'),dict(period_end='2025-12-31'),dict(anchors=['  '])])
def test_bad_observation_is_not_silently_normalized(change):
    with pytest.raises((ValueError,ArithmeticError)):
        observation(**change)


def test_units_and_percentage_are_explicit_and_no_midpoint_is_taken():
    obs=observation(metric='gross_margin',source_unit='percent',printed_low='20.63',printed_high='23.96')
    assert obs.bounds==(Decimal('.2063'),Decimal('.2396'))
    with pytest.raises(ValueError,match='metric/unit mismatch'):
        observation(metric='gross_margin',source_unit='CNY')


def test_growth_rejects_other_company_and_unused_conditions():
    obs=observation(ticker='SHSE:600519')
    with pytest.raises(ValueError,match='security mismatch'):
        calculate(rule('forward_ttm',['other'],annual_revenue_condition_cny='1000',next_h1_growth_condition='.2'),dict(other=obs))
    with pytest.raises(ValueError,match='unused forecast'):
        calculate(rule('growth',['current','prior'],annual_revenue_condition_cny='1000'),dict(current=observation(),prior=observation('prior')))


@pytest.fixture
def review_case(tmp_path, monkeypatch):
    from tools import review_operating_constraints as tool
    from tests.test_native_assumption_selection import sample
    from engine.data_dictionary import CompanyValuationInput
    from engine.orchestrator import run_full_valuation
    monkeypatch.setenv('ALPHALAKE_WORKSPACE',str(tmp_path))
    body=sample()
    replay=run_full_valuation(CompanyValuationInput.model_validate(body['inputs']))
    body.update(reference_snapshot={'id':'test'},equity_bridge=replay.equity_bridge,
                convertible_claim=replay.convertible_claim)
    report=tmp_path/'saved.json'
    report.write_text(json.dumps(body))
    content=b'test PDF placeholder; extractor mocked only in unit test'
    sha=hashlib.sha256(content).hexdigest()
    pdf=tmp_path/'cninfo'/'objects'/sha[:2]/(sha+'.pdf')
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(content)
    calls=[]
    def text(path, expected):
        calls.append(path)
        return '营业收入 单位：元 120 0\f毛利率 20.63'
    monkeypatch.setattr(tool,'verified_pdf_text',text)
    database=tmp_path/'snapshot.sqlite'
    with sqlite3.connect(database) as conn:
        conn.execute('CREATE TABLE metadata(key TEXT,value TEXT)')
        conn.executemany('INSERT INTO metadata VALUES (?,?)',[
            ('report_period','2025-12-31'),('reference_snapshot_id','test'),('information_as_of','2026-10-20T00:00:00+00:00')])
    def source(identifier,path,kind):
        return dict(id=identifier,kind=kind,file=path.relative_to(tmp_path).as_posix(),
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),locator='test',explanation='synthetic source')
    obs=observation(ticker='SHSE:600519').model_dump(mode='json')
    zero=observation('zero',ticker='SHSE:600519',printed_low='0').model_dump(mode='json')
    spec=dict(contract='operating-constraints-input-v1',sqlite_sha256=tool.digest(database),scope=['SHSE:600519'],
        sources=[source('baseline',report,'snapshot_baseline'),source('report',pdf,'reported_fact')],
        observations=[obs,zero],calculations=[],bindings=[dict(ticker='SHSE:600519',report_source='baseline')],
        cards=[dict(ticker='SHSE:600519',parameter=p,horizon='whole_path',support='directional',
            observations=['revenue'],calculations=[],interpretation='历史仅支持方向',missing='未来仍需条件')
            for p in ('growth','margin','capital')])
    return tool,spec,database,report,pdf,calls


def test_review_replays_native_report_caches_pdf_and_preserves_zero_and_inputs(review_case):
    tool,spec,database,report,pdf,calls=review_case
    before=deepcopy(spec),database.read_bytes(),report.read_bytes(),pdf.read_bytes()
    result=tool.review(spec,database)
    assert result['summary']==dict(companies=1,cards=3,support_counts={'directional':3})
    assert result['observations'][1]['normalized_low']=='0'
    assert len(calls)==1
    assert all(not c['automatic_adoption'] and c['future_parameter_bounds'] is None for c in result['cards'])
    assert before==(spec,database.read_bytes(),report.read_bytes(),pdf.read_bytes())


def test_quantitative_bridge_discloses_actual_forecast_difference_without_certifying_it(review_case):
    tool,spec,database,report,pdf,calls=review_case
    spec['calculations']=[dict(id='bridge',ticker='SHSE:600519',kind='forward_ttm',observations=['revenue'],
        annual_revenue_condition_cny='1000',next_h1_growth_condition='.2',condition='显式条件，不认证其合理性')]
    spec['cards'][0].update(support='quantitative_conditional',horizon='first_year',calculations=['bridge'])
    result=tool.review(spec,database)
    relation=result['cards'][0]['quantitative_relations'][0]
    native=Decimal(relation['native_first_year_revenue_cny'])
    assert relation['native_minus_relation_interval_cny']==[str(native-1024)]*2
    assert relation['alignment']=='difference_disclosed_not_automatic_acceptance'
    assert result['cards'][0]['future_parameter_bounds'] is None


@pytest.mark.parametrize('case', ['hash','amount','page','denominator','margin','snapshot','report'])
def test_review_rejects_evidence_and_contract_tampering(review_case,case):
    tool,spec,database,report,pdf,calls=review_case
    if case=='hash':
        pdf.write_bytes(b'changed PDF')
    elif case=='amount':
        spec['observations'][0]['printed_low']='121'
    elif case=='page':
        spec['observations'][0]['physical_page']=3
    elif case=='denominator':
        spec['cards'].pop()
    elif case=='margin':
        spec['cards'][1]['support']='quantitative_conditional'
    elif case=='snapshot':
        spec['sqlite_sha256']='0'*64
    else:
        body=json.loads(report.read_text());body['dcf']['revenue_projections'][0]+=1
        report.write_text(json.dumps(body))
        spec['sources'][0]['sha256']=tool.digest(report)
    with pytest.raises(ValueError):
        tool.review(spec,database)
