"""公司研究输入验证；不调用估值引擎、财务库或外网。"""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from engine.data_dictionary import CompanyValuationInput, RawFinancials
from tools import coverage_research as research


@pytest.fixture
def plan(tmp_path, monkeypatch):
    monkeypatch.setenv('ALPHALAKE_WORKSPACE', str(tmp_path))
    root = tmp_path/'research'/'authored'
    root.mkdir(parents=True)
    def save(name, body):
        path = root/name
        path.write_text(json.dumps(body, ensure_ascii=False))
        return path
    inputs = CompanyValuationInput(ticker='SHSE:600519', reporting_currency='CNY', stock_price_currency='CNY',
        macro_inputs=dict(risk_free_rate=.03, equity_risk_premium=.05, tax_rate_marginal=.25),
        industry_data=dict(industry_name='合成测试', beta_u=.7),
        raw_financials=[RawFinancials(fiscal_year=2025, revenues=1000., ebit=100.)]).model_dump(mode='json')
    snapshot = dict(sqlite_sha256='a'*64, reference_snapshot_id='reference-test')
    body = dict(ticker=inputs['ticker'], inputs=inputs, ltm_financials=dict(revenues=1000.),
        reference_snapshot=dict(id='reference-test'))
    baseline = save('baseline.json', dict(contract='initial-coverage-calculation-v2', ticker=inputs['ticker'],
        financial_input_snapshot=snapshot['sqlite_sha256'], admission='ready', response=body))
    company = save('company.json', dict(revenues=1000., research_expense=0,
        observation='合成公司有产品需求与投入资料，只供验证契约。'))
    reference = save('reference.json', dict(beta=.7, debt_cost=.05, boundary='参考代理，非公司预测。'))
    def evidence(identifier, kind, path, locator):
        return dict(id=identifier, kind=kind, file=path.relative_to(tmp_path).as_posix(),
            sha256=research.digest(path), locator=locator, explanation='明确来源角色，仅为合成回归。')
    claims = [dict(id=topic, statement='合成公司'+topic+'的研究条件及其适用范围。',
        parameters=[topic], evidence=['company' if topic in research.CORE else 'reference'],
        evidence_state='supported', counterevidence='若订单和回款不兑现则本条件需要修订。',
        revision_trigger='新报告披露后重看所采用年度路径。') for topic in research.TOPICS]
    claims[2]['parameters'].append('valuation_assumptions.annual_net_reinvestment')
    story = dict(id='company-condition', revision=1, ticker=inputs['ticker'], baseline_evidence='baseline',
        title='合成公司显式扩产条件', narrative='需求、竞争与投入合为一组研究判断，不是默认值自动延续。',
        economic_profile='普通非金融经营公司', mechanisms=['订单与交付', '投入与回款'], status='conditional',
        claims=claims, annual_operating_plan=[
            dict(year=1, revenue_million_cny=1100., adjusted_operating_margin=.12, tax_rate=.25),
            dict(year=2, revenue_million_cny=1210., adjusted_operating_margin=.13, tax_rate=.25)],
        assumption_overrides={'valuation_assumptions.annual_net_reinvestment': [80., 90.]},
        limitations=['未来实际需求与税法变化仍未知，不能称预测可靠性已经验证。'])
    basis = {topic: dict(claim_ids=[topic], rationale='观察与条件之间的公司具体推导；未来数值属于研究者判断。',
        decision='company_judgment' if topic in research.CORE else 'reference_proxy',
        limitations=['有正常不确定性，不意味着这项研究选择尚未作出。'], unresolved=[]) for topic in research.TOPICS}
    payload = dict(contract=research.CONTRACT, document=dict(contract='narrative-valuation-v2',
        snapshot=snapshot, scope={inputs['ticker']: 'nonfinancial_by_reference'},
        evidence=[evidence('baseline', 'snapshot_baseline', baseline, '$.response'),
                  evidence('company', 'reported_fact', company, '$.revenues；公司业务及投入说明'),
                  evidence('reference', 'reference', reference, '$.beta；代理适用条件')], stories=[story]),
        basis={story['id']: basis})
    plan_file = save('plan.json', payload)
    return plan_file, payload, tmp_path/'derived'/'research-freeze.json'


def assess(path, payload):
    return research.evaluate_plan(path, expected_scope=payload['document']['scope'],
        expected_snapshot=payload['document']['snapshot'])


def update(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False))


def test_explicit_company_plan_has_four_distinct_validation_dimensions(plan):
    path, payload, output = plan
    receipt = research.freeze_plan(path, output, expected_scope=payload['document']['scope'],
        expected_snapshot=payload['document']['snapshot'])
    result = research.assess_plan(output, expected_scope=payload['document']['scope'],
        expected_snapshot=payload['document']['snapshot'])
    assert receipt['frozen_at'] and result == receipt['assessment']
    assert result['counts'] == {'company_condition': 1}
    assert result['structure'] == 'passed' and result['arithmetic'] == 'not_evaluated'
    assert not result['economic_approval'] and result['prediction_validity'] == 'not_established'
    assert not result['automatic_parameter_selection']
    assert payload['document']['stories'][0]['limitations']
    with pytest.raises(ValueError, match='已存在'):
        research.freeze_plan(path, output, expected_scope=payload['document']['scope'],
            expected_snapshot=payload['document']['snapshot'])


def test_native_baseline_with_words_cannot_become_company_research(plan):
    path, payload, _ = plan
    for claim in payload['document']['stories'][0]['claims']:
        claim['evidence'] = ['baseline']
    update(path, payload)
    result = assess(path, payload)
    assert result['counts'] == {'pending': 1}
    assert all('baseline_is_not_research_basis' in item['issues'] for item in result['stories'][0]['basis'].values())


def test_core_company_basis_is_required_but_declared_proxy_and_normal_unknown_are_allowed(plan):
    path, payload, _ = plan
    payload['basis']['company-condition']['terminal']['unresolved'] = ['未来优势持续性未知，明确采用无超额回报条件。']
    update(path, payload)
    assert assess(path, payload)['counts'] == {'company_condition': 1}
    payload['document']['stories'][0]['claims'][0]['evidence'] = ['reference']
    update(path, payload)
    assert 'revenue:missing_company_evidence' in assess(path, payload)['stories'][0]['issues']


def test_unrelated_claim_cannot_lend_company_evidence_to_another_topic(plan):
    path, payload, _ = plan
    payload['document']['stories'][0]['claims'][0]['evidence'] = ['reference']
    payload['basis']['company-condition']['revenue']['claim_ids'].append('capital')
    update(path, payload)
    with pytest.raises(ValueError, match='主张绑定不一致'):
        assess(path, payload)


def test_frozen_request_cannot_drift_when_converter_changes(plan, monkeypatch):
    path, payload, output = plan
    receipt = research.freeze_plan(path, output, expected_scope=payload['document']['scope'],
        expected_snapshot=payload['document']['snapshot'])
    assert len(receipt['assessment']['stories'][0]['request_sha256']) == 64
    original = research.compile_story
    def changed(*args):
        request, binding = original(*args)
        request['inputs']['valuation_assumptions']['annual_net_reinvestment'][0] += 1
        return request, binding
    monkeypatch.setattr(research, 'compile_story', changed)
    with pytest.raises(ValueError, match='冻结版本不一致'):
        research.assess_plan(output, expected_scope=payload['document']['scope'],
            expected_snapshot=payload['document']['snapshot'])


@pytest.mark.parametrize('missing', [True, False])
def test_missing_or_unselected_important_choice_stays_pending(plan, missing):
    path, payload, _ = plan
    if missing:
        del payload['basis']['company-condition']['capital']
    else:
        payload['basis']['company-condition']['capital']['decision'] = 'unresolved'
    update(path, payload)
    assert assess(path, payload)['counts'] == {'pending': 1}


@pytest.mark.parametrize('path_name', ['raw_financials', 'valuation_assumptions.typo', 'valuation_assumptions.annual_forecast'])
def test_existing_story_converter_rejects_fact_override_typo_or_parallel_forecast(plan, path_name):
    path, payload, _ = plan
    story = payload['document']['stories'][0]
    story['assumption_overrides'] = {path_name: []}
    story['claims'][2]['parameters'] = ['capital', path_name]
    update(path, payload)
    with pytest.raises(ValueError, match='only existing|unknown native|operating path'):
        assess(path, payload)


def test_partial_annual_capital_path_uses_existing_typed_contract(plan):
    path, payload, _ = plan
    payload['document']['stories'][0]['assumption_overrides']['valuation_assumptions.annual_net_reinvestment'] = [80.]
    update(path, payload)
    with pytest.raises(ValueError, match='every projection year'):
        assess(path, payload)


def test_evidence_tamper_or_post_freeze_edit_is_rejected(plan):
    path, payload, output = plan
    research.freeze_plan(path, output, expected_scope=payload['document']['scope'], expected_snapshot=payload['document']['snapshot'])
    payload['basis']['company-condition']['revenue']['rationale'] = '计算后改写的研究理由。'
    update(path, payload)
    with pytest.raises(ValueError, match='计算后补写'):
        research.assess_plan(output, expected_scope=payload['document']['scope'], expected_snapshot=payload['document']['snapshot'])
    company = path.parent/'company.json'
    company.write_text('{"revenues":0}')
    with pytest.raises(ValueError, match='path/hash'):
        assess(path, payload)


def test_scope_snapshot_and_company_identity_cannot_be_mixed(plan):
    path, payload, _ = plan
    with pytest.raises(ValueError, match='范围或快照'):
        research.evaluate_plan(path, expected_scope={}, expected_snapshot=payload['document']['snapshot'])
    snapshot = payload['document']['snapshot'] | {'sqlite_sha256': 'b'*64}
    with pytest.raises(ValueError, match='范围或快照'):
        research.evaluate_plan(path, expected_scope=payload['document']['scope'], expected_snapshot=snapshot)
    baseline_file = path.parent/'baseline.json'
    source = json.loads(baseline_file.read_text())
    source['ticker'] = 'SZSE:300866'
    baseline_file.write_text(json.dumps(source))
    payload['document']['evidence'][0]['sha256'] = research.digest(baseline_file)
    update(path, payload)
    with pytest.raises(ValueError, match='证券身份'):
        assess(path, payload)


def test_declared_existing_narrative_baseline_is_reused_without_copying(plan):
    path, payload, _ = plan
    baseline_file = path.parent/'baseline.json'
    source = json.loads(baseline_file.read_text())
    baseline_file.write_text(json.dumps(dict(ticker=source['ticker'], http_status=200, body=source['response'])))
    payload['document']['evidence'][0].update(locator='$.body', sha256=research.digest(baseline_file))
    update(path, payload)
    assert assess(path, payload)['counts'] == {'company_condition': 1}
    payload['document']['evidence'][0]['locator'] = '$.body.inputs[0]'
    update(path, payload)
    with pytest.raises(ValueError, match='只允许'):
        assess(path, payload)


def test_financial_and_source_missing_companies_remain_in_full_scope(plan):
    path, payload, _ = plan
    original = payload['document']['stories'][0]
    for ticker, identifier, scope in [('SHSE:600000', 'bank', 'outside_financial_scope'),
                                      ('SZSE:301718', 'missing', 'nonfinancial_by_reference')]:
        baseline = path.parent/(identifier+'.json')
        baseline.write_text(json.dumps(dict(ticker=ticker, http_status=422, body=dict(detail='合成缺项／范围外'))))
        payload['document']['evidence'].append(dict(id=identifier, kind='snapshot_baseline',
            file=baseline.relative_to(path.parents[2]).as_posix(), sha256=research.digest(baseline),
            locator='$.body', explanation='范围外或缺项仍保留。'))
        story = deepcopy(original)
        story.update(id=identifier, ticker=ticker, baseline_evidence=identifier, status='draft', annual_operating_plan=[], assumption_overrides={})
        story['claims'][2]['parameters'] = ['capital']
        payload['document']['stories'].append(story)
        payload['document']['scope'][ticker] = scope
    update(path, payload)
    result = assess(path, payload)
    assert len(result['companies']) == 3
    assert result['counts'] == {'company_condition': 1, 'not_applicable': 1, 'pending': 1}


def test_sparse_batch_keeps_full_denominator_without_loading_other_baselines(plan):
    path, payload, _ = plan
    payload['document']['scope']['SZSE:301718'] = 'nonfinancial_by_reference'
    payload['document']['scope']['SHSE:600000'] = 'outside_financial_scope'
    update(path, payload)
    result = assess(path, payload)
    assert result['counts'] == {'company_condition': 1, 'not_in_batch': 2}
    assert result['companies'][1]['story_ids'] == []
    # The ordinary narrative contract still requires all of its own companies.
    with pytest.raises(ValueError, match='complete prepared company denominator'):
        research.Document.model_validate(payload['document'])
    payload['document']['scope'].pop('SHSE:600519')
    update(path, payload)
    with pytest.raises(ValueError, match='不属于完整发布范围'):
        assess(path, payload)


def test_sparse_batch_preserves_native_evidence_validation_and_rejects_old_plan(plan):
    path, payload, _ = plan
    payload['document']['scope']['SZSE:301718'] = 'nonfinancial_by_reference'
    broken = deepcopy(payload)
    broken['document']['stories'][0]['claims'][0]['evidence'] = ['unknown-source']
    update(path, broken)
    with pytest.raises(ValueError, match='registered evidence'):
        assess(path, broken)
    payload['contract'] = 'coverage-research-plan-v1'
    update(path, payload)
    with pytest.raises(ValueError, match='coverage-research-plan-v2'):
        assess(path, payload)


def test_analyst_batch_freezes_prose_and_keeps_native_baseline_out_of_company_evidence(plan, monkeypatch):
    from tools import coverage_research_notes as notes
    path, payload, frozen_file = plan
    root = path.parents[2]
    snapshot = payload['document']['snapshot'] | {'financial_report_period': '2026-06-30'}
    scope = payload['document']['scope'] | {'SZSE:301718': 'nonfinancial_by_reference'}
    ticker = 'SHSE:600519'
    results = path.parent/'results.json'
    results.write_text(json.dumps({'results': [dict(ticker=key, report_id=key) for key in scope]}))
    baseline = path.parent/'baseline.json'
    note_file = path.parent/'analyst.json'
    source = dict(path=(path.parent/'company.json').relative_to(root).as_posix(),
                  sha256=research.digest(path.parent/'company.json'), locator='$.observation')
    note = dict(format=notes.NOTE, ticker=ticker, company='合成公司', financial_period='2026-06-30',
        source_sqlite_sha256=snapshot['sqlite_sha256'], reference_snapshot=snapshot['reference_snapshot_id'],
        source_snapshot=[source, dict(path=baseline.relative_to(root).as_posix(), sha256=research.digest(baseline))],
        company_evidence=[source], analyst_status=notes.ADOPTED, title='人工合成条件',
        business_and_competition='公司需求与资本投入机制，仅测试结构，不代表实际公司。',
        thesis=dict(title='产品扩张', growth_claim='明确增长条件', margin_claim='明确利润条件',
            capital_claim='明确资本条件', counterevidence='投入不能形成订单与回款则反证'),
        parameter_basis=[dict(parameter='research', basis='普通费用研发')], unresolved=['未来需求未知'],
        annual_assumptions=[dict(year=1, growth=.1, adjusted_operating_margin=.12, tax_rate=.25,
            research_expense_ratio=0., sales_to_capital=2.)], capital_path_mode='annual_sales_to_capital',
        terminal=dict(growth=.01, marginal_roic=None, basis='无新增超额回报研究条件'),
        tax_and_research=dict(tax_path_is_analyst_policy=True, tax_basis='普通税率研究条件'))
    note_file.write_text(json.dumps(note, ensure_ascii=False))
    note_file.with_suffix('.md').write_text('计算前的公司判断与反证。')
    index_file = path.parent/'batch.json'
    index_file.write_text(json.dumps(dict(notes=[dict(ticker=ticker, structured=note_file.relative_to(root).as_posix(),
        note=note_file.with_suffix('.md').relative_to(root).as_posix(), sha256=research.digest(note_file))])))
    monkeypatch.setattr(notes, 'published_context', lambda _: (scope, snapshot, {}))
    monkeypatch.setattr(notes, 'read_report', lambda identifier: SimpleNamespace(id=identifier))
    def verified(report, field):
        selected = results if field == 'results' else baseline
        return SimpleNamespace(path=selected.relative_to(root).as_posix(), sha256=research.digest(selected)), selected
    monkeypatch.setattr(notes, 'verified_file', verified)
    output = root/'derived'/'plan.json'
    result = notes.prepare_batch(index_file, 'baseline', output, path.parent/'fake.sqlite')
    assert result['authored_companies'] == 1 and result['companies'] == 2
    research.freeze_plan(output, frozen_file, expected_scope=scope, expected_snapshot=snapshot)
    evidence = json.loads(output.read_text())['document']['evidence']
    baseline_refs = [item for item in evidence if item['file'] == baseline.relative_to(root).as_posix()]
    assert all(item['kind'] == 'snapshot_baseline' for item in baseline_refs)
    note_file.with_suffix('.md').write_text('计算之后偷偷改写正文。')
    with pytest.raises(ValueError, match='path/hash'):
        research.assess_plan(frozen_file, expected_scope=scope, expected_snapshot=snapshot)
    # 缺公司参考不阻止登记真实研究诊断，但不能产生可执行条件。
    note.update(analyst_status='pending_scope_review', reference_snapshot=None)
    note_file.write_text(json.dumps(note, ensure_ascii=False))
    index = json.loads(index_file.read_text())
    index['notes'][0]['sha256'] = research.digest(note_file)
    index_file.write_text(json.dumps(index))
    pending_output = root/'derived'/'pending-plan.json'
    notes.prepare_batch(index_file, 'baseline', pending_output, path.parent/'fake.sqlite')
    pending = research.evaluate_plan(pending_output, expected_scope=scope, expected_snapshot=snapshot)
    assert pending['counts'] == {'pending': 1, 'not_in_batch': 1}
    note['analyst_status'] = notes.ADOPTED
    note_file.write_text(json.dumps(note, ensure_ascii=False))
    index['notes'][0]['sha256'] = research.digest(note_file)
    index_file.write_text(json.dumps(index))
    with pytest.raises(ValueError, match='消费版本不符'):
        notes.prepare_batch(index_file, 'baseline', root/'derived'/'invalid-plan.json', path.parent/'fake.sqlite')
    note.update(analyst_status='complete_numeric_diagnostic_not_main_case',
        reference_snapshot=snapshot['reference_snapshot_id'])
    note_file.write_text(json.dumps(note, ensure_ascii=False))
    index['notes'][0].update(sha256=research.digest(note_file), purpose='diagnostic_condition')
    index_file.write_text(json.dumps(index))
    diagnostic_output = root/'derived'/'diagnostic-plan.json'
    notes.prepare_batch(index_file, 'baseline', diagnostic_output, path.parent/'fake.sqlite')
    diagnostic = research.evaluate_plan(diagnostic_output, expected_scope=scope, expected_snapshot=snapshot)
    assert diagnostic['counts'] == {'company_condition': 1, 'not_in_batch': 1}
    story = json.loads(diagnostic_output.read_text())['document']['stories'][0]
    assert story['title'].startswith('仅作诊断') and not diagnostic['economic_approval']
    index['notes'][0]['purpose'] = 'deferred'
    index_file.write_text(json.dumps(index))
    deferred_output = root/'derived'/'deferred-plan.json'
    notes.prepare_batch(index_file, 'baseline', deferred_output, path.parent/'fake.sqlite')
    deferred = research.evaluate_plan(deferred_output, expected_scope=scope, expected_snapshot=snapshot)
    assert deferred['counts'] == {'pending': 1, 'not_in_batch': 1}
    index['notes'][0]['purpose'] = 'guess_from_result'
    index_file.write_text(json.dumps(index))
    with pytest.raises(ValueError, match='研究执行用途'):
        notes.prepare_batch(index_file, 'baseline', root/'derived'/'invalid-purpose.json', path.parent/'fake.sqlite')


@pytest.mark.parametrize('rejected', [False, True])
def test_run_executes_only_frozen_company_conditions_and_keeps_rejections(plan, monkeypatch, rejected):
    from fastapi import testclient
    from tools import evaluate_native_policy, narrative_valuation, publish_native_valuation
    path, payload, output = plan
    baseline_file = path.parent/'baseline.json'
    source = json.loads(baseline_file.read_text())
    for field in ('adjusted', 'cost_of_capital', 'cashflow', 'dcf', 'final'):
        source['response'][field] = {}
    source['response']['final'] = {'value_per_share': 30.}
    baseline_file.write_text(json.dumps(source))
    payload['document']['evidence'][0]['sha256'] = research.digest(baseline_file)
    original = payload['document']['stories'][0]
    for ticker, identifier, scope in [('SHSE:600000', 'bank', 'outside_financial_scope'),
                                      ('SZSE:301718', 'pending', 'nonfinancial_by_reference')]:
        baseline = path.parent/(identifier+'.json')
        baseline.write_text(json.dumps(dict(ticker=ticker, http_status=422, body=dict(detail='合成缺项'))))
        payload['document']['evidence'].append(dict(id=identifier, kind='snapshot_baseline',
            file=baseline.relative_to(path.parents[2]).as_posix(), sha256=research.digest(baseline),
            locator='$.body', explanation='不计算的范围状态。'))
        story = deepcopy(original)
        story.update(id=identifier, ticker=ticker, baseline_evidence=identifier,
            status='draft', annual_operating_plan=[], assumption_overrides={})
        story['claims'][2]['parameters'] = ['capital']
        payload['document']['stories'].append(story)
        payload['document']['scope'][ticker] = scope
    database = path.parent/'consumption.sqlite'
    database.write_bytes(b'synthetic-only')
    payload['document']['snapshot']['sqlite_sha256'] = research.digest(database)
    source['financial_input_snapshot'] = payload['document']['snapshot']['sqlite_sha256']
    source['response']['id'] = 'synthetic-session'
    baseline_file.write_text(json.dumps(source))
    payload['document']['evidence'][0]['sha256'] = research.digest(baseline_file)
    update(path, payload)
    research.freeze_plan(path, output, expected_scope=payload['document']['scope'],
        expected_snapshot=payload['document']['snapshot'])
    calls = []
    class Client:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, endpoint, *, json):
            calls.append(('baseline', json['ticker']))
            return SimpleNamespace(status_code=200, json=lambda: source['response'])
        def delete(self, endpoint):
            calls.append(('release', endpoint))
            return SimpleNamespace(status_code=204)
    def calculate(client, request, metadata, web, **kwargs):
        calls.append(('calculate', request['inputs']['ticker']))
        if rejected:
            return None, {'reason': '合成原生拒绝，不能补0'}
        return {'final': {'value_per_share': 42.}}, {'arithmetic': 'passed'}
    monkeypatch.setenv('US_CN_HK_DB_PATH', 'synthetic-prior')
    monkeypatch.setattr(testclient, 'TestClient', Client)
    monkeypatch.setattr(research, 'published_context', lambda _: (payload['document']['scope'], payload['document']['snapshot'], {}))
    monkeypatch.setattr(publish_native_valuation, 'runtime_identity', lambda _: {'synthetic': True})
    monkeypatch.setattr(evaluate_native_policy, 'calculate_request', calculate)
    monkeypatch.setattr(narrative_valuation, 'check_operating_plan', lambda *args: None)
    result = research.run(output, database, output.parent/'run')
    assert len(result['results']) == 3
    assert calls == [('baseline', original['ticker']), ('release', '/api/valuation/synthetic-session'),
                     ('calculate', original['ticker'])]
    row = result['results'][0]
    assert row['status'] == ('rejected_company_condition' if rejected else 'calculated_company_condition')
    assert row['value_per_share'] == (None if rejected else 42.)
    assert result['results'][1]['status'] == 'not_applicable'
    assert result['results'][2]['status'] == 'pending'
