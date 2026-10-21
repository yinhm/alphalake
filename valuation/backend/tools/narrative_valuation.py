"""故事证据→显式年度输入→原生API→可重放结果；不选择参数或改变网页默认。"""
import argparse
from collections import Counter
from copy import deepcopy
import csv
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from data_sources.paths import workspace_path
from engine.data_dictionary import CompanyValuationInput, ForecastYear
from tools.check_native_sqlite import value_digest
from tools.compare_valuations import changes
from tools.csi300_valuation import check_cashflows
from tools.cyclical_valuation import load_scope
from tools.evaluate_native_policy import calculate_request
from tools.export_alphalake_sqlite import digest
from tools.publish_native_valuation import runtime_identity
from tools.review_native_policy import review_report

CONTRACT = 'narrative-valuation-v2'
METHOD = 'https://pages.stern.nyu.edu/~adamodar/New_Home_Page/NNPreface.html'
OPERATING = {'revenue', 'margin', 'tax'}
INHERITED = {'capital', 'research', 'financing', 'terminal'}
GENERATED = {'projection_years', 'annual_forecast', 'revenue_growth_next_year',
    'revenue_growth_years_2_5', 'operating_margin_next_year', 'target_operating_margin',
    'margin_convergence_year', 'effective_tax_rate_override_years_1_5', 'override_tax_convergence'}
BRIDGE = {'failure_probability', 'distress_proceeds_pct', 'failure_tie_to',
    'override_trapped_cash', 'trapped_cash_amount', 'trapped_cash_tax_rate'}
CLAIMS = {'convertible_debt', 'has_convertible', 'preferred_stock', 'has_preferred'}


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, strict=True, str_strip_whitespace=True)


class Evidence(Record):
    id: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{0,79}$')
    kind: Literal['snapshot_baseline', 'reported_fact', 'company_plan', 'reference', 'policy_proxy', 'research_judgment']
    file: str = Field(min_length=1)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    locator: str = Field(min_length=1)
    explanation: str = Field(min_length=1)


class Claim(Record):
    id: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{0,79}$')
    statement: str = Field(min_length=1)
    parameters: list[str] = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)
    evidence_state: Literal['supported', 'conflicting', 'insufficient']
    counterevidence: str = Field(min_length=1)
    revision_trigger: str = Field(min_length=1)


class OperatingYear(Record):
    year: int = Field(ge=1)
    revenue_million_cny: float = Field(gt=0)
    adjusted_operating_margin: float = Field(ge=-1, le=1)
    tax_rate: float = Field(ge=0, le=1)


class Story(Record):
    id: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{0,79}$')
    revision: int = Field(ge=1)
    ticker: str = Field(pattern=r'^(SHSE|SZSE):[0-9]{6}$')
    baseline_evidence: str
    title: str = Field(min_length=1)
    narrative: str = Field(min_length=1)
    economic_profile: str = Field(min_length=1)
    mechanisms: list[str] = Field(min_length=1)
    status: Literal['draft', 'conditional', 'not_applicable']
    claims: list[Claim] = Field(min_length=1)
    annual_operating_plan: list[OperatingYear]
    assumption_overrides: dict[str, Any]
    limitations: list[str] = Field(min_length=1)

    @model_validator(mode='after')
    def bindings(self):
        if len({c.id for c in self.claims}) != len(self.claims):
            raise ValueError('duplicate claim ID')
        required = OPERATING | INHERITED | set(self.assumption_overrides)
        bound = {p for c in self.claims for p in c.parameters}
        if bound != required:
            raise ValueError('claim bindings must cover operating, inherited and every override: '+str(sorted(required ^ bound)))
        if [r.year for r in self.annual_operating_plan] != list(range(1, len(self.annual_operating_plan)+1)):
            raise ValueError('consecutive actual forecast years required')
        if self.status == 'conditional' and not self.annual_operating_plan:
            raise ValueError('complete annual operating plan required')
        return self


class Document(Record):
    contract: Literal['narrative-valuation-v2']
    snapshot: dict[str, Any]
    scope: dict[str, str]
    evidence: list[Evidence]
    stories: list[Story] = Field(min_length=1)
    previous_run_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')

    @model_validator(mode='after')
    def references(self):
        evidence = {e.id:e for e in self.evidence}
        if len(evidence) != len(self.evidence) or len({s.id for s in self.stories}) != len(self.stories):
            raise ValueError('unique evidence and story IDs required; same ticker may have multiple stories')
        if {s.ticker for s in self.stories} != set(self.scope):
            raise ValueError('retain complete prepared company denominator')
        for story in self.stories:
            ids = {story.baseline_evidence} | {e for c in story.claims for e in c.evidence}
            if ids-set(evidence) or evidence[story.baseline_evidence].kind != 'snapshot_baseline':
                raise ValueError('every claim and baseline must reference registered evidence')
        for ticker in self.scope:
            if len({s.baseline_evidence for s in self.stories if s.ticker == ticker}) != 1:
                raise ValueError('all stories for a company must share one authoritative baseline')
        return self


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key: '+key)
            result[key] = value
        return result
    def nonfinite(value):
        raise ValueError('nonfinite JSON number: '+value)
    return json.loads(path.read_text(), object_pairs_hook=pairs, parse_constant=nonfinite)


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def new_output(path):
    path = path.resolve()
    if path.exists() or not path.is_relative_to(workspace_path('derived').resolve()):
        raise ValueError('new output under workspace/derived required; immutable prior runs')
    path.mkdir(parents=True)
    return path


def source_path(evidence):
    path = workspace_path(evidence.file).resolve(strict=True)
    if not path.is_relative_to(workspace_path().resolve()) or digest(path) != evidence.sha256:
        raise ValueError('workspace evidence path/hash mismatch: '+evidence.id)
    if path.suffix.lower() == '.pdf':
        relative = path.relative_to(workspace_path().resolve())
        if relative.parts[0] == 'derived':
            raise ValueError('PDF must reference a source archive, not a research copy')
        if relative.parts[0] == 'cninfo' and relative.as_posix() != 'cninfo/objects/'+evidence.sha256[:2]+'/'+evidence.sha256+'.pdf':
            raise ValueError('CNINFO PDF must reference its canonical source object')
        if evidence.file != relative.as_posix():
            raise ValueError('PDF evidence must use its canonical workspace-relative path')
    return path


def prepare(database, coverage, tickers, output):
    """只生成待审草稿，完整保留范围外和缺项，不批准来源初值为预测。"""
    from fastapi.testclient import TestClient
    from api.main import app
    database, coverage = database.resolve(strict=True), coverage.resolve(strict=True)
    snapshot_hash = digest(database)
    with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True) as conn:
        metadata = dict(conn.execute("SELECT key,value FROM metadata WHERE key < 'input_context:' OR key >= 'input_context;'"))
        all_tickers = [r[0] for r in conn.execute('SELECT ticker FROM companies ORDER BY ticker')]
    if metadata.get('contract') != 'alphalake-sqlite-v13':
        raise ValueError('current native SQLite contract required')
    scopes = load_scope(coverage, snapshot_hash, metadata, all_tickers)
    if not tickers or len(set(tickers)) != len(tickers) or set(tickers)-set(scopes):
        raise ValueError('unique published company tickers required')
    os.environ['US_CN_HK_DB_PATH'] = str(database)
    output = new_output(output)
    evidence, stories = [], []
    with TestClient(app, raise_server_exceptions=False) as client:
        for ticker in tickers:
            response = client.post('/api/valuation/from-database', json=dict(ticker=ticker))
            body = response.json()
            key = ticker.lower().replace(':', '-')
            path = output/(key+'-baseline.json')
            save(path, dict(ticker=ticker, http_status=response.status_code, body=body))
            evidence.append(dict(id=key+'-baseline', kind='snapshot_baseline',
                file=str(path.relative_to(workspace_path().resolve())), sha256=digest(path),
                locator='原生数据库响应；inputs、source_metadata、valuation_proxy及reference_snapshot',
                explanation='既有事实、参考和来源初值；不是审核完成的公司未来预测'))
            plan = []
            if response.status_code == 200:
                try:
                    if body['reference_snapshot']['id'] != metadata['reference_snapshot_id']:
                        raise ValueError('prepared reference publication mismatch')
                    dcf = body['dcf']
                    values = list(zip(dcf['revenue_projections'], dcf['ebit_projections'], dcf['tax_rate_projections'], strict=True))
                    if all(r > 0 and -1 <= e/r <= 1 and 0 <= t <= 1 for r, e, t in values):
                        plan = [dict(year=i+1, revenue_million_cny=r, adjusted_operating_margin=e/r, tax_rate=t)
                            for i, (r, e, t) in enumerate(values)]
                finally:
                    if client.delete('/api/valuation/'+body['id']).status_code != 204:
                        raise ValueError('preparation session release failed')
            stories.append(dict(id=key+'-source-condition', revision=1, ticker=ticker, baseline_evidence=key+'-baseline',
                title='待审：来源初值条件', narrative='先说明业务、竞争、资源及终点，再明确采用的年度条件。',
                economic_profile='待核实', mechanisms=['待核实'], status='draft',
                claims=[dict(id='inherited', statement='当前只继承原生来源初值；未审核经营预测。',
                    parameters=sorted(OPERATING | INHERITED), evidence=[key+'-baseline'], evidence_state='insufficient',
                    counterevidence='历史和行业来源不能证明未来路径；需要公司机制与竞争证据。',
                    revision_trigger='核对公司业务后更新主张、年度条件及反例，显式改为conditional才能计算。')],
                annual_operating_plan=plan, assumption_overrides={}, limitations=[
                    '当前草稿不执行估值；来源初值不是合理参数自动选择器。',
                    '未来融资、研发税务及稳态条件尚沿基线；有限寿命等仍受原引擎边界限制。']))
    document = Document.model_validate(dict(contract=CONTRACT, snapshot=dict(sqlite_sha256=snapshot_hash,
        metadata=metadata, coverage_sha256=digest(coverage),
        coverage_file=str(coverage.relative_to(workspace_path().resolve())), method_source=METHOD),
        scope={t:scopes[t] for t in tickers}, evidence=evidence, stories=stories))
    if digest(database) != snapshot_hash:
        raise ValueError('SQLite changed during story preparation')
    save(output/'stories.json', document.model_dump(mode='json'))
    return dict(contract=CONTRACT, status='drafts_prepared', companies=len(tickers), stories=len(stories), file=str(output/'stories.json'))


def compile_story(story, baseline):
    """仅机械转换明确收入金额及既有参数；不估计或选择数字。"""
    if baseline['ticker'] != story.ticker or baseline['http_status'] != 200:
        raise ValueError('matching successful native baseline required')
    body = baseline['body']
    inputs = CompanyValuationInput.model_validate(body['inputs'])
    if inputs.ticker != story.ticker or (inputs.reporting_currency, inputs.stock_price_currency) != ('CNY', 'CNY'):
        raise ValueError('matching CNY SH/SZ company required')
    payload = deepcopy(inputs.model_dump(mode='json'))
    for path, value in story.assumption_overrides.items():
        parts = path.split('.')
        if parts[0] not in ('valuation_assumptions', 'macro_inputs', 'methodology_choices') or len(parts) < 2:
            raise ValueError('only existing assumption/reference paths may be overridden: '+path)
        if parts[0] == 'valuation_assumptions' and parts[1] in GENERATED:
            raise ValueError('operating path fields come from annual_operating_plan: '+path)
        if parts[0] == 'valuation_assumptions' and parts[1] in BRIDGE:
            raise ValueError('story must preserve original equity/claim bridge: '+path)
        if parts[0] == 'methodology_choices' and parts[1] in CLAIMS:
            raise ValueError('story must preserve original equity/claim bridge: '+path)
        target = payload
        for part in parts[:-1]:
            if not isinstance(target, dict) or part not in target:
                raise ValueError('unknown native override path: '+path)
            target = target[part]
        if not isinstance(target, dict) or parts[-1] not in target:
            raise ValueError('unknown native override path: '+path)
        target[parts[-1]] = deepcopy(value)
    previous = body['ltm_financials']['revenues']
    if previous is None or previous <= 0:
        raise ValueError('positive base revenue required; zero-revenue commercialization not expressed by this path')
    annual = []
    for row in story.annual_operating_plan:
        annual.append(ForecastYear(growth=row.revenue_million_cny/previous-1,
            margin=row.adjusted_operating_margin, tax=row.tax_rate).model_dump())
        previous = row.revenue_million_cny
    payload['valuation_assumptions'].update(projection_years=len(annual), annual_forecast=annual)
    a = payload['valuation_assumptions']
    if a['annual_net_reinvestment'] is None:
        lag = a['reinvestment_lag_years'] if a['override_reinvestment_lag'] else 1
        if not 0 <= lag <= 3:
            raise ValueError('native ratio investment lag only supports 0..3; do not silently truncate story')
    typed = CompanyValuationInput.model_validate(payload)
    canonical = typed.model_dump(mode='json')
    for path, value in story.assumption_overrides.items():
        actual = canonical
        for part in path.split('.'):
            actual = actual[part]
        if actual != value:
            raise ValueError('override was ignored or normalized unexpectedly: '+path)
    permitted = ('/valuation_assumptions/', '/macro_inputs/', '/methodology_choices/')
    # Typed日期会把同一UTC瞬间的+00:00写成Z，先统一基线表示再核对事实。
    input_changes = list(changes(inputs.model_dump(mode='json'), canonical))
    if any(not c['path'].startswith(permitted) for c in input_changes):
        raise ValueError('story changed financial facts, identity or equity bridge')
    return dict(inputs=canonical), dict(input_changes=input_changes,
        claim_bindings={p:[c.id for c in story.claims if p in c.parameters]
            for p in sorted(OPERATING | INHERITED | set(story.assumption_overrides))},
        evidence_states=dict(Counter(c.evidence_state for c in story.claims)),
        inherited_assumptions='未覆盖条件沿同版基线；不认证公司未来融资、税务及稳态依据',
        economic_forecast_verified=False, automatic_adoption=False)


def check_operating_plan(story, body):
    rows = zip(story.annual_operating_plan, body['dcf']['revenue_projections'],
        body['dcf']['ebit_projections'], body['dcf']['tax_rate_projections'], strict=True)
    for expected, revenue, ebit, tax in rows:
        if not all(math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-6) for a, b in (
                (expected.revenue_million_cny, revenue),
                (expected.revenue_million_cny*expected.adjusted_operating_margin, ebit), (expected.tax_rate, tax))):
            raise ValueError('actual native operating path differs from story')
    check_cashflows(body)


def render(output, document, summary):
    """中文可读交付；计算数值只取已有API响应，不另写估值公式。"""
    with (output/'summary.csv').open('w', newline='') as stream:
        fields = ['ticker', 'story_id', 'title', 'status', 'baseline_value_per_share',
            'value_per_share', 'difference_per_share', 'terminal_share', 'live_verified', 'reason']
        writer = csv.DictWriter(stream, fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(summary['results'])
    lines = ['# 故事到估值', '', f"运行：`{summary['run_id']}`", '',
        '本报告为显式条件；不自动采用、不赋发生概率、不认证公司预测。财务事实和网页默认不修改。', '',
        f"财务／股本基期：{document.snapshot['metadata'].get('report_period')}；信息截止：{document.snapshot['metadata'].get('information_as_of')}。金额百万元人民币，每股值人民币元。", '',
        f"公司 {summary['companies']} 家，故事 {summary['stories']} 条；状态：{summary['statuses']}。", '',
        '| 公司 | 故事 | 状态 | 原默认每股 | 故事每股 | 差额 |', '|---|---|---|---:|---:|---:|']
    def cell(value):
        if value is None:
            return '—'
        return f'{value:.2f}' if isinstance(value, (float, int)) else str(value).replace('|', '／').replace('\n', ' ')
    for row in summary['results']:
        lines.append('| '+' | '.join(cell(row.get(k)) for k in ('ticker', 'title', 'status',
            'baseline_value_per_share', 'value_per_share', 'difference_per_share'))+' |')
    evidence = {e.id:e for e in document.evidence}
    for story, row in zip(document.stories, summary['results'], strict=True):
        lines += ['', f'## {story.ticker}：{story.title}', '', story.narrative, '',
            f'经济画像：{story.economic_profile}；机制：{", ".join(story.mechanisms)}。', '']
        for claim in story.claims:
            lines += [f'- **{claim.statement}**（证据状态：{claim.evidence_state}）',
                f'  - 参数：{", ".join(claim.parameters)}。',
                f'  - 反例：{claim.counterevidence}', f'  - 修订触发：{claim.revision_trigger}']
            for key in claim.evidence:
                source = evidence[key]
                link = (os.path.relpath(workspace_path(source.file), output) if Path(source.file).suffix.lower() == '.pdf'
                        else f'evidence/{source.id}{Path(source.file).suffix}')
                lines.append(f'  - 依据 [{source.id}]({link})：{source.locator}；{source.explanation}')
        lines += ['', '限制：'+'；'.join(story.limitations), '']
        if row['status'] == 'calculated_conditional':
            body = read_json(output/(story.id+'-result.json'))
            reviewed = read_json(output/(story.id+'-review.json'))
            releases = [r['year'] for r in reviewed['forecast']
                if r.get('implied_nonresearch_net_reinvestment_million_cny', 0) < -1e-8]
            lines += [f"终值现值占经营价值：{cell(row['terminal_share']*100) if row['terminal_share'] is not None else '—'}%。", '',
                '| 年 | 收入（百万元） | 调整EBIT | 现金税 | NOPAT | 总净投入 | FCFF |', '|---:|---:|---:|---:|---:|---:|---:|']
            d = body['dcf']
            for i, values in enumerate(zip(d['revenue_projections'], d['ebit_projections'],
                    d['cash_tax_projections'], d['nopat_projections'], d['reinvestment_projections'], d['fcff_projections'], strict=True)):
                lines.append('| '+' | '.join([str(i+1), *map(cell, values)])+' |')
            lines += ['', f'[完整输入]({story.id}-request.json) · [原生结果]({story.id}-result.json) · [经济审阅]({story.id}-review.json)',
                '', '算术重放通过；经营依据仍按主张和反例人工审阅，未来预测有效性未验证。']
            if releases:
                lines += ['', f'资本边界：第{releases}年隐含非研发资本净释放，仍需可回收性及组成依据。']
            if reviewed['economic_checks']['terminal_transition']['requires_capital_transition_basis']:
                lines += ['', '终值边界：末年投入与稳定期g/ROIC规则仍有转换，需要经营及资本依据；未自动平滑或调整。']
            lines += ['', f"经营价值：{cell(d['value_of_operating_assets'])}百万元；原生股权价值：{cell(d['value_of_equity'])}百万元；",
                f"最终每股条件值：{cell(body['final']['value_per_share'])}元，既有股权／股本及期权处理详见完整原生结果。"]
        else:
            lines.append('未计算原因：'+row.get('reason', row['status']))
    (output/'report.md').write_text('\n'.join(lines)+'\n')


def run(database, stories_file, output, web=None):
    from fastapi.testclient import TestClient
    from api.main import app
    started = time.monotonic()
    database, stories_file = database.resolve(strict=True), stories_file.resolve(strict=True)
    document = Document.model_validate(read_json(stories_file))
    before, source_hash = digest(database), digest(stories_file)
    with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True) as conn:
        metadata = dict(conn.execute("SELECT key,value FROM metadata WHERE key < 'input_context:' OR key >= 'input_context;'"))
        tickers = [r[0] for r in conn.execute('SELECT ticker FROM companies ORDER BY ticker')]
    if metadata != document.snapshot['metadata'] or before != document.snapshot['sqlite_sha256']:
        raise ValueError('story/source snapshot mismatch; prepare a new explicit revision')
    coverage = workspace_path(document.snapshot['coverage_file']).resolve(strict=True)
    if digest(coverage) != document.snapshot['coverage_sha256']:
        raise ValueError('canonical coverage changed')
    scopes = load_scope(coverage, before, metadata, tickers)
    if document.scope != {t:scopes[t] for t in document.scope}:
        raise ValueError('story scope differs from canonical publication')
    sources = {e.id:source_path(e) for e in document.evidence}
    output = new_output(output)
    (output/'evidence').mkdir()
    pdf_references = {}
    for key, path in sources.items():
        if path.suffix.lower() == '.pdf':
            pdf_references[key] = dict(file=str(path.relative_to(workspace_path().resolve())), sha256=digest(path))
        else:
            shutil.copyfile(path, output/'evidence'/(key+path.suffix))
    shutil.copyfile(coverage, output/'coverage.json')
    save(output/'stories.json', document.model_dump(mode='json'))
    runtime = runtime_identity(database.parent)
    identity = dict(contract=CONTRACT, document=value_digest(document.model_dump(mode='json')), runtime=runtime)
    run_id = value_digest(identity)
    save(output/'protocol.json', dict(identity=identity, run_id=run_id, frozen_at=datetime.now(timezone.utc).isoformat(),
        original_story_sha256=source_hash, method_source=METHOD, web=web, selected_story=None, automatic_adoption=False))
    os.environ['US_CN_HK_DB_PATH'] = str(database)
    rows, validated = [], set()
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            for story in document.stories:
                baseline = read_json(output/'evidence'/(story.baseline_evidence+sources[story.baseline_evidence].suffix))
                row = dict(ticker=story.ticker, story_id=story.id, revision=story.revision, title=story.title,
                    status=story.status, live_verified=False, automatic_adoption=False)
                rows.append(row)
                if baseline['ticker'] != story.ticker:
                    raise ValueError('baseline ticker mismatch')
                if document.scope[story.ticker] != 'nonfinancial_by_reference':
                    row.update(status='outside_scope', reason=document.scope[story.ticker]); continue
                if baseline['http_status'] != 200:
                    row.update(status='blocked_source_inputs', reason=str(baseline['body'])); continue
                row['baseline_value_per_share'] = baseline['body']['final']['value_per_share']
                if story.status != 'conditional':
                    row['reason'] = '待审草稿' if story.status == 'draft' else '当前原生路径不适用；'+ ';'.join(story.limitations)
                    continue
                if story.ticker not in validated:
                    actual = client.post('/api/valuation/from-database', json=dict(ticker=story.ticker))
                    if actual.status_code != 200:
                        raise ValueError('previously prepared baseline no longer available')
                    body = actual.json()
                    try:
                        for field in ('inputs', 'ltm_financials', 'adjusted', 'cost_of_capital', 'cashflow', 'dcf', 'final', 'reference_snapshot'):
                            if body[field] != baseline['body'][field]:
                                raise ValueError('baseline differs from authoritative native source: '+field)
                    finally:
                        if client.delete('/api/valuation/'+body['id']).status_code != 204:
                            raise ValueError('baseline verification session release failed')
                    validated.add(story.ticker)
                try:
                    request, binding = compile_story(story, baseline)
                    save(output/(story.id+'-request.json'), request)
                    save(output/(story.id+'-binding.json'), binding)
                    body, review = calculate_request(client, request, metadata, web)
                    if body is None:
                        row.update(review); continue
                    check_operating_plan(story, body)
                    save(output/(story.id+'-result.json'), body)
                    save(output/(story.id+'-review.json'), review)
                    operating = body['dcf']['value_of_operating_assets']
                    row.update(status='calculated_conditional', value_per_share=body['final']['value_per_share'],
                        difference_per_share=body['final']['value_per_share']-row['baseline_value_per_share'],
                        terminal_share=body['dcf']['pv_terminal_value']/operating if operating else None,
                        live_verified=bool(web), evidence_states=binding['evidence_states'],
                        verification=dict(input_integrity='passed', arithmetic='passed',
                            economic_basis='requires_claim_review', prediction_validity='not_evaluated'))
                except ValueError as error:
                    row.update(status='rejected_story_input', reason=str(error))
        if digest(database) != before or digest(stories_file) != source_hash or digest(coverage) != document.snapshot['coverage_sha256']:
            raise ValueError('source changed during narrative valuation')
        for e in document.evidence:
            if digest(sources[e.id]) != e.sha256:
                raise ValueError('story evidence changed during valuation: '+e.id)
        summary = dict(contract=CONTRACT, run_id=run_id, companies=len(document.scope), stories=len(rows),
            statuses=dict(Counter(r['status'] for r in rows)), selected_story=None, automatic_adoption=False,
            database_unchanged=True, economic_forecast_verified=False, elapsed_seconds=time.monotonic()-started, results=rows)
        save(output/'summary.json', summary)
        render(output, document, summary)
        artifacts = {str(p.relative_to(output)):digest(p) for p in sorted(output.rglob('*')) if p.is_file()}
        save(output/'manifest.json', dict(contract=CONTRACT, run_id=run_id, identity=identity,
            artifacts=artifacts, pdf_references=pdf_references))
        return {k:v for k,v in summary.items() if k != 'results'}
    except Exception as error:
        save(output/'failure.json', dict(status='failed_incomplete_run', reason=str(error), results=rows))
        raise


def verify(output):
    """校验归档、重新编译、原生引擎重放与独立现金流聚合。"""
    output = output.resolve(strict=True)
    manifest = read_json(output/'manifest.json')
    if manifest['contract'] != CONTRACT or value_digest(manifest['identity']) != manifest['run_id']:
        raise ValueError('run identity mismatch')
    for name, expected in manifest['artifacts'].items():
        path = (output/name).resolve(strict=True)
        if not path.is_relative_to(output) or digest(path) != expected:
            raise ValueError('artifact path/hash mismatch: '+name)
    actual_files = {str(p.relative_to(output)) for p in output.rglob('*') if p.is_file() and p != output/'manifest.json'}
    if actual_files != set(manifest['artifacts']):
        raise ValueError('unregistered or missing artifact')
    document = Document.model_validate(read_json(output/'stories.json'))
    if value_digest(document.model_dump(mode='json')) != manifest['identity']['document']:
        raise ValueError('story document identity mismatch')
    runtime_matches = runtime_identity(workspace_path()) == manifest['identity']['runtime']
    summary = read_json(output/'summary.json')
    if summary['run_id'] != manifest['run_id'] or len(summary['results']) != len(document.stories):
        raise ValueError('summary run/story denominator mismatch')
    if (summary['companies'] != len(document.scope) or summary['stories'] != len(document.stories)
        or summary['statuses'] != dict(Counter(r['status'] for r in summary['results']))
        or summary['selected_story'] is not None or summary['automatic_adoption'] is not False):
        raise ValueError('summary counts or adoption differs from explicit contract')
    evidence = {e.id:e for e in document.evidence}
    expected_pdf = {e.id:dict(file=e.file, sha256=e.sha256) for e in document.evidence if Path(e.file).suffix.lower() == '.pdf'}
    if manifest['pdf_references'] != expected_pdf:
        raise ValueError('PDF references differ from registered source evidence')
    for e in document.evidence:
        path = source_path(e) if e.id in expected_pdf else output/'evidence'/(e.id+Path(e.file).suffix)
        if digest(path) != e.sha256:
            raise ValueError('archived evidence differs from registered source: '+e.id)
    if digest(output/'coverage.json') != document.snapshot['coverage_sha256']:
        raise ValueError('archived canonical coverage differs')
    checked = 0
    for story, row in zip(document.stories, summary['results'], strict=True):
        if row['story_id'] != story.id or row['ticker'] != story.ticker:
            raise ValueError('story/result association mismatch')
        if row['status'] != 'calculated_conditional':
            continue
        e = evidence[story.baseline_evidence]
        baseline = read_json(output/'evidence'/(e.id+Path(e.file).suffix))
        request, binding = compile_story(story, baseline)
        body = read_json(output/(story.id+'-result.json'))
        if request != read_json(output/(story.id+'-request.json')) or binding != read_json(output/(story.id+'-binding.json')):
            raise ValueError('compiled request or claim binding differs')
        returned = CompanyValuationInput.model_validate(body['inputs'])
        supplied = CompanyValuationInput.model_validate(request['inputs'])
        supplied.company_metrics = returned.company_metrics
        if supplied != returned:
            raise ValueError('saved native response changed explicit inputs')
        if body['reference_snapshot']['id'] != document.snapshot['metadata']['reference_snapshot_id']:
            raise ValueError('saved response/reference publication mismatch')
        if review_report(body) != read_json(output/(story.id+'-review.json')):
            raise ValueError('saved economic review differs')
        check_operating_plan(story, body)
        if (row['value_per_share'] != body['final']['value_per_share']
            or row['baseline_value_per_share'] != baseline['body']['final']['value_per_share']
            or row['difference_per_share'] != row['value_per_share']-row['baseline_value_per_share']):
            raise ValueError('summary conditional value differs')
        checked += 1
    return dict(contract=CONTRACT, run_id=manifest['run_id'], status='verified', stories=len(document.stories),
        calculated=checked, runtime_matches=runtime_matches,
        runtime_verification='同保存版本' if runtime_matches else '实现版本已变；当前版本重新编译和逐项重放仍一致，保留两端版本')


def compare(before, after):
    left, right = verify(before), verify(after)
    a, b = read_json(before/'summary.json'), read_json(after/'summary.json')
    da, db = read_json(before/'stories.json'), read_json(after/'stories.json')
    if db['previous_run_id'] != left['run_id']:
        raise ValueError('explicit previous_run_id required for story revision')
    old = {(r['ticker'], r['story_id']):r for r in a['results']}
    old_stories = {(s['ticker'], s['id']):s for s in da['stories']}
    for story in db['stories']:
        previous = old_stories.get((story['ticker'], story['id']))
        if previous and story != previous and story['revision'] <= previous['revision']:
            raise ValueError('changed story requires increasing revision number')
    rows = []
    for row in b['results']:
        previous = old.get((row['ticker'], row['story_id']))
        rows.append(dict(ticker=row['ticker'], story_id=row['story_id'], before=previous, after=row,
            difference_per_share=row['value_per_share']-previous['value_per_share']
                if previous and 'value_per_share' in previous and 'value_per_share' in row else None))
    return dict(contract=CONTRACT, before_run_id=left['run_id'], after_run_id=right['run_id'],
        document_changes=list(changes(da, db)), results=rows,
        removed_stories=[r for key,r in old.items() if key not in {(r['ticker'], r['story_id']) for r in b['results']}],
        attribution='输入与结果差异；多因素变化及交互不作单因素归因', automatic_adoption=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare', help='同版SQLite准备待审故事草稿')
    p.add_argument('--database', type=Path, required=True)
    p.add_argument('--coverage', type=Path, required=True)
    p.add_argument('--ticker', action='append', required=True)
    p.add_argument('--output', type=Path, required=True)
    p = commands.add_parser('run', help='显式故事→年度输入→原生API→中文报告')
    p.add_argument('--database', type=Path, required=True)
    p.add_argument('--stories', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--web', help='现有网页完整API的实际HTTP复验')
    p = commands.add_parser('verify', help='归档完整性、请求重建和引擎重放')
    p.add_argument('run_dir', type=Path)
    p = commands.add_parser('compare', help='比较明确关联的两个故事版本')
    p.add_argument('before', type=Path)
    p.add_argument('after', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            result = prepare(args.database, args.coverage, args.ticker, args.output)
        elif args.command == 'run':
            result = run(args.database, args.stories, args.output, args.web)
        elif args.command == 'verify':
            result = verify(args.run_dir)
        else:
            result = compare(args.before, args.after)
    except (ValueError, KeyError, OSError, sqlite3.Error) as error:
        print(json.dumps(dict(contract=CONTRACT, status='rejected', command=args.command, reason=str(error)), ensure_ascii=False))
        raise SystemExit(2)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
