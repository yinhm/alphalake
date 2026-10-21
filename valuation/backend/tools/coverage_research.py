"""冻结逐公司研究条件并核对证据；不选择预测参数或计算公司价值。"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Literal

from pydantic import Field, model_validator

from data_sources.paths import workspace_path
from tools.check_native_sqlite import value_digest
from tools.narrative_valuation import (
    Document, Record, compile_story, digest, read_json, source_path,
)

CONTRACT = 'coverage-research-plan-v1'
FROZEN = 'coverage-research-frozen-v1'
TOPICS = ('revenue', 'margin', 'capital', 'research', 'tax', 'financing', 'terminal')
CORE = {'revenue', 'margin', 'capital'}


class Basis(Record):
    claim_ids: list[str] = Field(min_length=1)
    rationale: str = Field(min_length=1)
    decision: Literal['company_judgment', 'reference_proxy', 'unresolved']
    limitations: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)


class ResearchPlan(Record):
    contract: Literal['coverage-research-plan-v1']
    document: Document
    basis: dict[str, dict[Literal['revenue', 'margin', 'capital', 'research', 'tax', 'financing', 'terminal'], Basis]]

    @model_validator(mode='after')
    def references(self):
        stories = {story.id: story for story in self.document.stories}
        if set(self.basis) - set(stories):
            raise ValueError('研究依据引用了未登记故事')
        for identifier, topics in self.basis.items():
            claims = {claim.id: claim for claim in stories[identifier].claims}
            for topic, basis in topics.items():
                if (len(set(basis.claim_ids)) != len(basis.claim_ids)
                        or set(basis.claim_ids) - set(claims)
                        or not all(topic in claims[key].parameters for key in basis.claim_ids)):
                    raise ValueError('研究依据与主张绑定不一致：'+identifier+'/'+topic)
        return self


def relative_path(path):
    resolved = Path(path).resolve(strict=True)
    if not resolved.is_relative_to(workspace_path().resolve()) or not resolved.is_file():
        raise ValueError('研究计划与证据必须位于workspace内')
    return resolved.relative_to(workspace_path().resolve()).as_posix()


def baseline_source(story, evidence, snapshot):
    """只读取声明的两种原生响应位置，不执行JSONPath或复制来源。"""
    packet = read_json(source_path(evidence))
    if packet.get('ticker') != story.ticker:
        raise ValueError('研究基线证券身份不一致：'+story.ticker)
    if evidence.locator == '$.response':
        if (packet.get('contract') != 'initial-coverage-calculation-v2'
                or packet.get('financial_input_snapshot') != snapshot['sqlite_sha256']):
            raise ValueError('研究基线不属于指定消费快照')
        baseline = dict(ticker=story.ticker, http_status=(200 if packet.get('admission') == 'ready'
                        else packet['http_status']), body=packet['response'])
    elif evidence.locator == '$.body':
        baseline = {key: packet[key] for key in ('ticker', 'http_status', 'body')}
    else:
        raise ValueError('研究基线只允许$.response或$.body定位')
    if baseline['http_status'] == 200:
        body = baseline['body']
        reference = snapshot.get('reference_snapshot_id') or snapshot.get('metadata', {}).get('reference_snapshot_id')
        if (body.get('ticker', story.ticker) != story.ticker or body['inputs'].get('ticker') != story.ticker
                or not reference or body.get('reference_snapshot', {}).get('id') != reference):
            raise ValueError('研究基线响应的证券或参考版本不一致')
    return baseline


def evaluate_plan(plan_file, *, expected_scope, expected_snapshot):
    """校验研究者声明及typed输入；判断质量和未来实现不由工具认证。"""
    plan = ResearchPlan.model_validate(read_json(Path(plan_file)))
    document = plan.document
    if document.scope != expected_scope or document.snapshot != expected_snapshot:
        raise ValueError('研究范围或快照与冻结目标不一致')
    evidence = {item.id: item for item in document.evidence}
    verified = {}
    for item in evidence.values():
        key = (item.file, item.sha256)
        if key not in verified:
            verified[key] = source_path(item)
        if item.file != relative_path(verified[key]):
            raise ValueError('研究证据须使用规范workspace相对路径')
    stories = []
    for story in document.stories:
        baseline = baseline_source(story, evidence[story.baseline_evidence], document.snapshot)
        claims = {claim.id: claim for claim in story.claims}
        topics, issues, reviewed, request_sha256 = {}, [], set(), None
        for topic in TOPICS:
            basis = plan.basis.get(story.id, {}).get(topic)
            blockers = []
            if basis is None:
                blockers.append('missing_basis')
            else:
                reviewed.update(basis.claim_ids)
                sources = [evidence[key] for identifier in basis.claim_ids for key in claims[identifier].evidence]
                if basis.decision == 'unresolved':
                    blockers.append('decision_unresolved')
                if not any(item.kind != 'snapshot_baseline' for item in sources):
                    blockers.append('baseline_is_not_research_basis')
                if topic in CORE and not any(item.kind in ('reported_fact', 'company_plan') for item in sources):
                    blockers.append('missing_company_evidence')
            topics[topic] = dict(decision=basis.decision if basis else 'unresolved', issues=blockers,
                claim_ids=basis.claim_ids if basis else [], limitations=basis.limitations if basis else [],
                unresolved=basis.unresolved if basis else [])
            issues.extend(topic+':'+blocker for blocker in blockers)
        if set(claims) - reviewed:
            issues.append('unreviewed_claims:'+','.join(sorted(set(claims) - reviewed)))
        scope = document.scope[story.ticker]
        if scope != 'nonfinancial_by_reference':
            status = 'not_applicable' if scope == 'outside_financial_scope' else 'pending'
            issues.append('product_scope:'+scope)
        elif baseline['http_status'] != 200:
            status = 'pending'
            issues.append('source_inputs_unavailable')
        elif story.status == 'not_applicable':
            status = 'not_applicable'
        else:
            if story.status != 'conditional':
                issues.append('research_draft')
            # Reuse the existing converter to reject forbidden paths and incomplete typed inputs.
            if story.status == 'conditional':
                request, _ = compile_story(story, baseline)
                request_sha256 = value_digest(request)
            status = 'pending' if issues else 'company_condition'
        stories.append(dict(id=story.id, ticker=story.ticker, revision=story.revision,
            research_status=status, issues=issues, basis=topics,
            request_sha256=request_sha256,
            claim_evidence_states={claim.id: claim.evidence_state for claim in story.claims},
            structure='passed', arithmetic='not_evaluated', economic_approval=False,
            prediction_validity='not_established'))
    companies, by_company = [], defaultdict(list)
    for item in stories:
        by_company[item['ticker']].append(item)
    for ticker in document.scope:
        items = by_company[ticker]
        states = {item['research_status'] for item in items}
        state = ('company_condition' if 'company_condition' in states else
                 'not_applicable' if states == {'not_applicable'} else 'pending')
        companies.append(dict(ticker=ticker, research_status=state,
            story_ids=[item['id'] for item in items],
            condition_story_ids=[item['id'] for item in items if item['research_status'] == 'company_condition']))
    return dict(contract=CONTRACT, companies=companies, stories=stories,
        counts=dict(Counter(item['research_status'] for item in companies)),
        structure='passed', arithmetic='not_evaluated', economic_approval=False,
        prediction_validity='not_established', automatic_parameter_selection=False)


def freeze_plan(plan_file, output, *, expected_scope, expected_snapshot):
    """在计算前写唯一冻结收据；后续编辑原计划必须另建研究版本。"""
    plan_file, output = Path(plan_file).resolve(strict=True), Path(output)
    relative_path(plan_file)
    if not output.resolve().is_relative_to(workspace_path('derived').resolve()):
        raise ValueError('冻结研究收据须放workspace/derived')
    if output.exists():
        raise ValueError('研究冻结收据已存在；修订须使用新路径')
    before = digest(plan_file)
    assessment = evaluate_plan(plan_file, expected_scope=expected_scope, expected_snapshot=expected_snapshot)
    if digest(plan_file) != before:
        raise ValueError('研究计划在冻结期间变化')
    receipt = dict(contract=FROZEN, frozen_at=datetime.now(timezone.utc).isoformat(),
        plan_file=relative_path(plan_file), plan_sha256=before, assessment=assessment)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(receipt, ensure_ascii=False, allow_nan=False, indent=2)+'\n')
    return receipt


def assess_plan(frozen_file, *, expected_scope, expected_snapshot):
    receipt = read_json(Path(frozen_file))
    if receipt.get('contract') != FROZEN:
        raise ValueError('须提供计算前冻结的研究收据')
    instant = datetime.fromisoformat(receipt['frozen_at'])
    if instant.utcoffset() is None:
        raise ValueError('研究冻结时间必须带时区')
    plan_file = workspace_path(receipt['plan_file'])
    if receipt['plan_file'] != relative_path(plan_file) or digest(plan_file) != receipt['plan_sha256']:
        raise ValueError('研究计划已变化，不能在计算后补写依据')
    assessment = evaluate_plan(plan_file, expected_scope=expected_scope, expected_snapshot=expected_snapshot)
    if digest(plan_file) != receipt['plan_sha256'] or assessment != receipt['assessment']:
        raise ValueError('研究计划或证据审阅状态与冻结版本不一致')
    return assessment


def published_context(database):
    """从当前消费快照取得完整范围；研究不能自行缩小发布分母。"""
    from data_sources.us_cn_hk_db import input_contexts, publication_scope, snapshot_metadata
    with sqlite3.connect(Path(database).resolve(strict=True).as_uri()+'?mode=ro', uri=True) as connection:
        metadata = snapshot_metadata(connection)
        scope = {row['ticker']: row['model_scope']['status'] for row in input_contexts(connection)}
        if set(scope) != {row['ticker'] for row in publication_scope(connection)}:
            raise ValueError('研究范围必须保留完整已发布规范证券')
    snapshot = dict(sqlite_sha256=digest(database), source_database_sha256=metadata['source_database_sha256'],
        reference_snapshot_id=metadata['reference_snapshot_id'], information_as_of=metadata['information_as_of'],
        financial_report_period=metadata['report_period'])
    return scope, snapshot, metadata


def run(frozen_file, database, output, *, tickers=None, web=None):
    """只执行已冻结的公司条件；待研究不回退为默认预测。"""
    from fastapi.testclient import TestClient
    from api.main import app
    from engine.data_dictionary import CompanyValuationInput
    from tools.evaluate_native_policy import calculate_request
    from tools.narrative_valuation import check_operating_plan
    from tools.publish_native_valuation import runtime_identity
    from tools.initial_coverage import save

    started = time.monotonic()
    scope, snapshot, metadata = published_context(database)
    assessment = assess_plan(frozen_file, expected_scope=scope, expected_snapshot=snapshot)
    receipt = read_json(Path(frozen_file))
    plan = ResearchPlan.model_validate(read_json(workspace_path(receipt['plan_file'])))
    selected = set(tickers or scope)
    if not selected or not selected <= set(scope):
        raise ValueError('研究计算证券不属于冻结的完整范围')
    output = Path(output)
    if output.exists() or not output.resolve().is_relative_to(workspace_path('derived').resolve()):
        raise ValueError('研究计算须使用workspace/derived内新的运行目录')
    runtime = runtime_identity(workspace_path())
    frozen_hash = digest(frozen_file)
    run_id = value_digest(dict(frozen_sha256=frozen_hash, runtime=runtime, tickers=sorted(selected)))
    output.mkdir(parents=True)
    save(output/'protocol.json', dict(contract='coverage-research-run-v1', run_id=run_id,
        frozen_file=relative_path(frozen_file), frozen_sha256=frozen_hash, snapshot=snapshot,
        runtime=runtime, selected_tickers=sorted(selected), selected_is_full_scope=selected == set(scope),
        frozen_at=receipt['frozen_at'], automatic_adoption=False))
    evidence = {item.id: item for item in plan.document.evidence}
    statuses = {item['id']: item for item in assessment['stories']}
    rows, validated = [], set()
    os.environ['US_CN_HK_DB_PATH'] = str(Path(database).resolve())
    with TestClient(app, raise_server_exceptions=False) as client:
        for story in plan.document.stories:
            state = statuses[story.id]
            row = dict(ticker=story.ticker, story_id=story.id, research_status=state['research_status'],
                status='not_selected' if story.ticker not in selected else state['research_status'],
                issues=state['issues'], value_per_share=None, model_file=None)
            rows.append(row)
            if row['status'] != 'company_condition':
                continue
            baseline = baseline_source(story, evidence[story.baseline_evidence], snapshot)
            if story.ticker not in validated:
                actual = client.post('/api/valuation/from-database', json=dict(ticker=story.ticker))
                if actual.status_code != 200:
                    raise ValueError('研究基线不再有合格当前输入：'+story.ticker)
                body = actual.json()
                try:
                    saved = baseline['body']
                    for field in ('ltm_financials', 'adjusted', 'cost_of_capital', 'cashflow', 'dcf', 'final', 'reference_snapshot'):
                        if body[field] != saved[field]:
                            raise ValueError('研究基线与当前原生输入不一致：'+story.ticker+'/'+field)
                    if CompanyValuationInput.model_validate(body['inputs']) != CompanyValuationInput.model_validate(saved['inputs']):
                        raise ValueError('研究基线的实际输入已变化：'+story.ticker)
                finally:
                    if client.delete('/api/valuation/'+body['id']).status_code != 204:
                        raise ValueError('研究基线核验会话未释放')
                validated.add(story.ticker)
            request, binding = compile_story(story, baseline)
            if value_digest(request) != state['request_sha256']:
                raise ValueError('实际研究请求与计算前冻结版本不一致：'+story.ticker)
            body, review = calculate_request(client, request, metadata, web, source_context=baseline['body'])
            if body is None:
                row.update(status='rejected_company_condition', diagnostic=review)
                continue
            check_operating_plan(story, body)
            model = dict(contract='coverage-company-research-result-v1', ticker=story.ticker, story_id=story.id,
                run_id=run_id, snapshot=snapshot, frozen_sha256=frozen_hash, request=request,
                request_sha256=state['request_sha256'],
                response=body, review=review, binding=binding, basis=state['basis'],
                baseline_evidence=evidence[story.baseline_evidence].model_dump(mode='json'),
                automatic_adoption=False, economic_approval=False, prediction_validity='not_established')
            model_path = output/(story.id+'.json')
            save(model_path, model)
            row.update(status='calculated_company_condition', value_per_share=body['final']['value_per_share'],
                baseline_value_per_share=baseline['body']['final']['value_per_share'],
                model_file=relative_path(model_path), model_sha256=digest(model_path), arithmetic='passed',
                live_verified=bool(web))
    if digest(database) != snapshot['sqlite_sha256']:
        raise ValueError('研究计算期间消费快照已变化')
    assess_plan(frozen_file, expected_scope=scope, expected_snapshot=snapshot)
    summary = dict(contract='coverage-research-run-v1', run_id=run_id, snapshot=snapshot, companies=len(scope),
        selected_companies=len(selected), selected_is_full_scope=selected == set(scope),
        research_counts=assessment['counts'], statuses=dict(Counter(row['status'] for row in rows)), results=rows,
        economic_approval=False, prediction_validity='not_established', automatic_adoption=False,
        elapsed_seconds=time.monotonic()-started)
    save(output/'summary.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('freeze', 'check', 'run'):
        command = commands.add_parser(name)
        command.add_argument('input', type=Path)
        command.add_argument('--database', type=Path, default=workspace_path('valuation.sqlite'))
        if name != 'check':
            command.add_argument('--output', type=Path, required=True)
        if name == 'run':
            command.add_argument('--ticker', action='append')
            command.add_argument('--web')
    args = parser.parse_args()
    try:
        scope, snapshot, _ = published_context(args.database)
        if args.command == 'freeze':
            value = freeze_plan(args.input, args.output, expected_scope=scope, expected_snapshot=snapshot)['assessment']
        elif args.command == 'check':
            value = assess_plan(args.input, expected_scope=scope, expected_snapshot=snapshot)
        else:
            value = run(args.input, args.database, args.output, tickers=args.ticker, web=args.web)
        print(json.dumps({key: item for key, item in value.items() if not isinstance(item, list)}, ensure_ascii=False))
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(1, str(error)+'\n')


if __name__ == '__main__':
    main()
