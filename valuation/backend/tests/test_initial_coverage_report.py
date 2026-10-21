"""初次覆盖不得把缺项、时期错配或资本变化包装成已验证估值。"""
from copy import deepcopy
import io
import json

import pytest

from tools.initial_coverage_report import (
    catalogue_pages, company_facts, company_observations, company_report, disclosed_points, digest, guidance_rows,
    partial_company_report, price_comparison, protocol_resource,
    read_guidance, verify_materials, verify_model,
)
from tools.check_native_sqlite import value_digest


def test_latest_disclosure_keeps_adverse_operating_evidence_not_only_headline_growth():
    business = dict(reviews=[dict(period='2026-06-30', locator='$.jyps[0]', text=(
        '上半年公司营业收入达到一百亿元且同比增长百分之二十，产品销量增加。'
        '尽管收入增长，公司主要产品售价下降、客户延迟验收，盈利承受明显压力。'
        '公司存货及应收增长占用了现金，部分回款出现延迟，后续仍需观察。'
        '公司计划在未来两年新增研发投入200万元，尚未形成实际产品收入。'))])
    points = disclosed_points(business)
    assert {'增长与交付', '利润与竞争', '投入与回款'}.issubset({p['theme'] for p in points})
    assert any('售价下降' in p['text'] for p in points)
    assert any('回款出现延迟' in p['text'] for p in points)
    assert any(p['role'] == '公司计划，尚未兑现' for p in points)


def test_guidance_preserves_cumulative_period_metric_status_and_company_reasons():
    row = dict(source='eastmoney', period_start='2026-01-01', period_end='2026-09-30',
        period_basis='ytd', semantic_status='partial', announcement_date='2026-10-20',
        first_observed_at=None, lifecycle='superseded', source_stale=True, verification='linked',
        forecast_parent_net_income_lower=0, forecast_parent_net_income_upper=10,
        forecast_parent_net_income_unit='CNY', forecast_parent_net_income_status='valid',
        reasons=['公司称价格下降使利润承压，尚不是对EBIT的直接估计。'])
    cells = guidance_rows([row])[0]
    assert '2026-01-01至2026-09-30／年初累计' in cells[0]
    assert '0.00～10.00 CNY（指标状态：valid）' in cells[1]
    assert '公司解释（非独立验证）' in cells[1] and '价格下降' in cells[1]
    assert 'partial／superseded／来源过期' in cells[2]


def test_post_period_capital_keeps_condition_value_but_suppresses_price_difference():
    row = dict(ticker='SZSE:300866')
    protocol = dict(company_context={row['ticker']: dict(
        price_comparison_status='unreconciled_post_period_capital_change',
        reason='财报期后现金、发行股数与索偿尚未成套衔接。')})
    difference, status, reason = price_comparison(row, protocol, 200, 100)
    assert difference is None and status == 'unreconciled_post_period_capital_change'
    assert '发行股数' in reason
    assert price_comparison(row, {}, 200, 100)[0] == 1
    assert '资本基期代理' in price_comparison(row, {}, 200, 100)[2]
    assert price_comparison(row, {}, 200, None)[0] is None


def test_zero_revenue_forecast_keeps_undefined_margin_in_complete_report():
    from tests.test_native_cashflow_bridge import inputs, saved
    from tools.review_native_policy import review_report
    value = inputs()
    value.raw_financials[0].revenues = 0
    body = saved(value)
    review = review_report(body)
    assert all(r['revenue_million_cny'] == 0 and r['adjusted_operating_margin'] is None for r in review['forecast'])
    row = dict(ticker=value.ticker, name='合成零收入企业', quote_date=None, industry='合成测试')
    protocol = dict(financial_report_period='2026-06-30', information_as_of='2026-10-20T15:00:00Z')
    model = dict(response=body, review=review, request=dict(inputs=body['inputs']), sensitivities=[], run_id='synthetic')
    business = dict(report_end='2026-06-30', segments=[], reviews=[])
    profile = dict(profile=dict(ORG_NAME=row['name'], MAIN_BUSINESS='合成测试业务'), source_page=None)
    mechanism = dict(meaning='合成测试', observe='等待真实收入', counterevidence='收入未形成')
    text = company_report(row, protocol, model, {}, business, profile, mechanism, [], [], [])
    assert '未来收入增长 | 未取得' in text and '调整经营利润率 | 未取得' in text
    assert '**公司研究尚未完成。**' in text and '每股原生基线条件价值' in text
    assert review['forecast'][0]['adjusted_operating_margin'] is None


def test_same_period_observations_keep_actual_zero_and_do_not_create_forecast():
    facts = {
        '2025-06-30': dict(values_cny=dict(revenue_cumulative='100000000', reported_ebit='20000000', inventories='30000000')),
        '2026-06-30': dict(values_cny=dict(revenue_cumulative='0', reported_ebit='-10000000', inventories='40000000')),
    }
    rows = company_observations(facts, {}, '2026-06-30')
    assert '1.00→0.00亿元' in rows[0][1] and '-100.00%' in rows[0][1]
    assert '20.00%→未取得' in rows[1][1]
    assert '存货 0.30→0.40亿元' in rows[2][1]
    assert all('未计算DCF' in row[2] for row in rows)


def test_partial_company_has_known_business_and_missing_inputs_without_fake_dcf():
    row = dict(ticker='SHSE:600000', name='合成公司', admission='outside_product_scope',
        financial_admission='ready', missing_fields=[], reason='outside_product_scope')
    protocol = dict(financial_report_period='2026-06-30', information_as_of='2026-10-20T15:00:00Z',
        frozen_at='2026-10-21T08:00:00Z')
    model = dict(reason='金融业务暂不纳入当前非金融DCF。', response={'detail': 'not applicable'})
    business = dict(segments=[], reviews=[])
    profile = dict(profile=dict(MAIN_BUSINESS='合成银行的金融服务，仅为测试，不作估值。'))
    text = partial_company_report(row, protocol, model, {}, business, profile, {}, [])
    assert '金融业务暂不纳入' in text and '财务输入状态`ready`' in text
    assert '合成银行的金融服务' in text and '本次没有每股DCF条件值' in text
    assert '每股条件价值 0' not in text and '未列必需财务缺项' in text
    assert '未设估值股本基期' in text and '财务／股本基期2026-06-30' not in text


def materials():
    protocol = dict(source_database_sha256='source', sqlite_sha256='sqlite', financial_report_period='2026-06-30',
        frozen_at='2026-10-21T08:00:00Z', information_as_of='2026-10-20T15:00:00Z',
        reference_snapshot_id='references', companies=[dict(ticker='SHSE:600519')])
    financial = dict(source_database_sha256='source', sqlite_sha256='sqlite', report_end='2026-06-30',
        research_information_as_of=protocol['frozen_at'], valuation_information_as_of=protocol['information_as_of'],
        contract='initial-coverage-standard-facts-v2', company_offsets={'SHSE:600519': [0, 1]},
        companies_count=1, company_facts_file='financial-facts.jsonl', company_facts_sha256='facts', differences=[])
    collection = dict(protocol_sha256='protocol', records={})
    return protocol, financial, collection


def model_inputs():
    from engine.data_dictionary import CompanyValuationInput
    return CompanyValuationInput(ticker='SHSE:600519', reporting_currency='CNY',
        macro_inputs=dict(risk_free_rate=.03, equity_risk_premium=.06, tax_rate_marginal=.25),
        industry_data=dict(industry_name='合成测试', beta_u=.7)).model_dump(mode='json')


@pytest.fixture
def financial_materials(tmp_path, monkeypatch):
    protocol, financial, collection = materials()
    path = tmp_path/'financial-facts.jsonl'
    path.write_text(json.dumps(dict(ticker='SHSE:600519', source_periods={}))+'\n')
    financial['company_offsets']['SHSE:600519'] = [0, path.stat().st_size]
    financial['company_facts_sha256'] = digest(path)
    monkeypatch.setenv('ALPHALAKE_WORKSPACE', str(tmp_path))
    return protocol, financial, collection


@pytest.mark.parametrize('changed', ['sqlite_sha256', 'source_database_sha256', 'report_end', 'valuation_information_as_of'])
def test_stale_material_header_is_rejected_even_when_company_model_is_new(changed, financial_materials):
    protocol, financial, collection = financial_materials
    verify_materials(protocol, 'protocol', financial, collection)
    financial[changed] = 'previous'
    with pytest.raises(ValueError, match='标准研究资料'):
        verify_materials(protocol, 'protocol', financial, collection)


def test_stale_collection_or_truncated_financial_scope_is_not_treated_as_full_coverage(financial_materials):
    protocol, financial, collection = financial_materials
    collection['protocol_sha256'] = 'previous'
    with pytest.raises(ValueError, match='经营资料'):
        verify_materials(protocol, 'protocol', financial, collection)
    collection['protocol_sha256'] = 'protocol'
    financial['company_offsets'] = {}
    with pytest.raises(ValueError, match='标准研究资料'):
        verify_materials(protocol, 'protocol', financial, collection)


def test_streamed_company_facts_reject_tampered_identity_or_truncated_rows(financial_materials):
    _, financial, _ = financial_materials
    raw = json.dumps(dict(ticker='SHSE:600000', source_periods={})).encode()+b'\n'
    financial['company_offsets']['SHSE:600519'] = [0, len(raw)]
    with pytest.raises(ValueError, match='身份错配'):
        company_facts(io.BytesIO(raw), financial, 'SHSE:600519')
    with pytest.raises(ValueError, match='截断'):
        company_facts(io.BytesIO(raw[:-1]), financial, 'SHSE:600519')


@pytest.mark.parametrize('changed', ['ticker', 'admission', 'financial_input_snapshot'])
def test_other_company_or_snapshot_model_is_rejected(changed):
    protocol, _, _ = materials()
    inputs = model_inputs()
    row = dict(ticker='SHSE:600519', admission='ready', inputs_sha256=value_digest(inputs))
    model = dict(contract='initial-coverage-calculation-v2', protocol_sha256='protocol', ticker=row['ticker'],
        admission='ready', financial_input_snapshot='sqlite', api_verified=True,
        request=dict(inputs=inputs), response=dict(ticker=row['ticker'], inputs=inputs, reference_snapshot=dict(id='references')))
    verify_model(model, row, protocol, 'protocol')
    altered = deepcopy(model)
    altered[changed] = 'different'
    with pytest.raises(ValueError, match='冻结身份'):
        verify_model(altered, row, protocol, 'protocol')


def test_unverified_api_or_different_reference_is_rejected():
    protocol, _, _ = materials()
    inputs = model_inputs()
    row = dict(ticker='SHSE:600519', admission='ready', inputs_sha256=value_digest(inputs))
    model = dict(contract='initial-coverage-calculation-v2', protocol_sha256='protocol', ticker=row['ticker'],
        admission='ready', financial_input_snapshot='sqlite', api_verified=False,
        request=dict(inputs=inputs), response=dict(ticker=row['ticker'], inputs=inputs, reference_snapshot=dict(id='references')))
    with pytest.raises(ValueError, match='本地API'):
        verify_model(model, row, protocol, 'protocol')
    model['api_verified'] = True
    model['response']['reference_snapshot']['id'] = 'previous'
    with pytest.raises(ValueError, match='本地API'):
        verify_model(model, row, protocol, 'protocol')


def test_matching_top_level_identity_does_not_allow_another_company_payload():
    protocol, _, _ = materials()
    inputs = model_inputs()
    row = dict(ticker='SHSE:600519', admission='ready', inputs_sha256=value_digest(inputs))
    model = dict(contract='initial-coverage-calculation-v2', protocol_sha256='protocol', ticker=row['ticker'],
        admission='ready', financial_input_snapshot='sqlite', api_verified=True,
        request=dict(inputs=inputs), response=dict(ticker='SHSE:600000', inputs=dict(ticker='SHSE:600000'),
            reference_snapshot=dict(id='references')))
    with pytest.raises(ValueError, match='请求或响应身份'):
        verify_model(model, row, protocol, 'protocol')


def test_api_normalization_accepts_same_utc_instant_and_numbers_but_rejects_real_input_change():
    from engine.data_dictionary import RawFinancials
    protocol, _, _ = materials()
    inputs = model_inputs()
    inputs['prepared_ttm'] = dict(financials=RawFinancials(fiscal_year=2026, revenues=100, ebit=20).model_dump(mode='json'),
        period_start='2025-07-01', period_end='2026-06-30', information_as_of='2026-10-20T15:00:00Z',
        currency='CNY', money_unit='million_reporting_currency', shares_unit='million_shares', provenance={'source': 'synthetic'})
    inputs['methodology_choices']['convertible_debt']['face_value'] = 0
    normalized = deepcopy(inputs)
    normalized['prepared_ttm']['information_as_of'] = '2026-10-20T15:00:00+00:00'
    normalized['methodology_choices']['convertible_debt']['face_value'] = 0.0
    row = dict(ticker=inputs['ticker'], admission='ready', inputs_sha256=value_digest(inputs))
    model = dict(contract='initial-coverage-calculation-v2', protocol_sha256='protocol', ticker=row['ticker'],
        admission='ready', financial_input_snapshot='sqlite', api_verified=True,
        request=dict(inputs=inputs), response=dict(ticker=row['ticker'], inputs=normalized, reference_snapshot=dict(id='references')))
    verify_model(model, row, protocol, 'protocol')
    for changed in ('instant', 'forecast'):
        altered = deepcopy(model)
        if changed == 'instant':
            altered['response']['inputs']['prepared_ttm']['information_as_of'] = '2026-10-20T15:00:01Z'
        else:
            altered['response']['inputs']['valuation_assumptions']['revenue_growth_next_year'] = .123
        with pytest.raises(ValueError, match='请求或响应身份'):
            verify_model(altered, row, protocol, 'protocol')


def test_catalogue_paginates_every_security_and_does_not_fill_partial_values(tmp_path):
    rows = [dict(ticker=f'SHSE:{600000+i}', name='合成公司'+str(i), url='/research/company-'+str(i),
        admission='ready' if i % 2 else 'blocked_required_inputs', financial_admission='ready' if i % 2 else 'blocked',
        condition_value_cny_per_share=12 if i % 2 else None, close_cny=10, close_date='2026-10-20',
        descriptive_difference_pct=20 if i % 2 else None) for i in range(501)]
    files, pages = catalogue_pages(tmp_path, rows)
    assert [int(row[2]) for row in pages] == [250, 250, 1]
    rendered = '\n'.join(path.read_text() for _, _, path in files)
    assert rendered.count('](/research/company-') == 501
    assert 'SHSE:600500' in rendered and '未取得' in rendered and '未比较' in rendered
    assert all(path.stat().st_size < 2*1024*1024 for _, _, path in files)


def test_protocol_resource_must_remain_inside_workspace(tmp_path, monkeypatch):
    root = tmp_path/'workspace'
    root.mkdir()
    source = root/'source.json'
    source.write_text('{}')
    monkeypatch.setenv('ALPHALAKE_WORKSPACE', str(root))
    assert protocol_resource(dict(profile_inventory='source.json'), 'profile_inventory') == source
    with pytest.raises(ValueError, match='相对路径'):
        protocol_resource(dict(profile_inventory=str(source)), 'profile_inventory')


def test_guidance_requires_explicit_frozen_source_and_rejects_scope_pollution(tmp_path, monkeypatch):
    monkeypatch.setenv('ALPHALAKE_WORKSPACE', str(tmp_path))
    protocol = dict(companies=[dict(ticker='SHSE:600519'), dict(ticker='SZSE:300866')])
    path = tmp_path/'guidance.json'
    packet = dict(contract_version='earnings-guidance-context-v1', companies=[dict(ticker='SHSE:600519')],
        observations=[dict(ticker='SHSE:600519')])
    path.write_text(json.dumps(packet))
    assert read_guidance(protocol) == dict(companies=[], observations=[])
    protocol['guidance_file'] = 'guidance.json'
    assert read_guidance(protocol)['observations'] == [dict(ticker='SHSE:600519')]
    packet['observations'].append(dict(ticker='BSE:920001'))
    path.write_text(json.dumps(packet))
    with pytest.raises(ValueError, match='证券范围'):
        read_guidance(protocol)
    packet['observations'].pop()
    packet['contract_version'] = 'previous'
    path.write_text(json.dumps(packet))
    with pytest.raises(ValueError, match='查询契约'):
        read_guidance(protocol)
