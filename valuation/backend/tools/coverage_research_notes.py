"""将已写明的分析师笔记机械装配为研究批次；不选择参数或计算估值。"""
import argparse
import json
from pathlib import Path

from data_sources.paths import workspace_path
from data_sources.research_reports import read_report, verified_file
from tools.coverage_research import CONTRACT, TOPICS, ResearchPlan, published_context, relative_path
from tools.narrative_valuation import digest, read_json, source_path, Evidence

NOTE = 'alphalake-analyst-research-note-v1'
ADOPTED = 'compile_condition_with_disclosed_open_items'


def authored_story(note, baseline, key):
    """只展开研究者已提供的年度增长、费用率和资本条件。"""
    years = note['annual_assumptions']
    if not baseline['inputs']['adjustment_inputs']['has_r_and_d'] and any(
            year['research_expense_ratio'] != 0 for year in years):
        raise ValueError('当前基线未接通研发调整，不能忽略非零未来研发条件：' + key)
    revenue = baseline['ltm_financials']['revenues']
    operating, expenses = [], []
    for year in years:
        revenue *= 1 + year['growth']
        operating.append(dict(year=year['year'], revenue_million_cny=revenue,
            adjusted_operating_margin=year['adjusted_operating_margin'], tax_rate=year['tax_rate']))
        expenses.append(revenue * year['research_expense_ratio'])
    mode = note['capital_path_mode']
    if mode == 'annual_sales_to_capital':
        overrides = {'valuation_assumptions.annual_sales_to_capital': [year['sales_to_capital'] for year in years]}
    elif mode == 'annual_net_reinvestment':
        overrides = {'valuation_assumptions.annual_sales_to_capital': None,
            'valuation_assumptions.annual_net_reinvestment': [year['annual_net_reinvestment_million_cny'] for year in years]}
    else:
        raise ValueError('未知资本条件；不得自行选择：' + str(mode))
    terminal = note['terminal']
    overrides.update({'valuation_assumptions.roic_stable_override': terminal['marginal_roic'],
        'valuation_assumptions.stable_growth_rate': terminal['growth']})
    if baseline['inputs']['adjustment_inputs']['has_r_and_d']:
        terminal_expense = expenses[-1] * (1 + terminal['growth'])
        overrides['valuation_assumptions.research_tax'] = dict(policy_id=key+'-ordinary-rd',
            expenses=expenses, deductions=expenses, terminal_expense=terminal_expense,
            terminal_deduction=terminal_expense,
            tax_rate_basis=('taxable_profit_rate' if note['tax_and_research']['tax_path_is_analyst_policy']
                            else 'reported_effective_rate_proxy'))
    return operating, overrides


def prepare_batch(index_file, baseline_report_id, output, database):
    root = workspace_path().resolve()
    index_file, output = Path(index_file), Path(output)
    if output.exists() or not output.resolve().is_relative_to(workspace_path('derived').resolve()):
        raise ValueError('研究计划须使用workspace/derived内的新路径')
    scope, snapshot, _ = published_context(database)
    report = read_report(baseline_report_id)
    _, result_file = verified_file(report, 'results')
    baseline_rows = {row['ticker']: row for row in read_json(result_file)['results']}
    if set(baseline_rows) != set(scope):
        raise ValueError('基线登记与完整发布范围不一致')
    index = read_json(index_file)
    if not index.get('notes'):
        raise ValueError('须提供已完成的分析师批次索引')
    evidence = [dict(id='analyst-batch-index', kind='research_judgment', file=relative_path(index_file),
        sha256=digest(index_file), locator='$.notes；$.adoption_basis',
        explanation='计算前分析师采用清单，不自动选择参数或以历史原稿替代已声明修订。')]
    shared = ['analyst-batch-index']
    if index.get('capital_review'):
        review_path = root/index['capital_review']
        review = dict(id='batch-capital-review', kind='research_judgment', file=index['capital_review'],
            sha256=index['capital_review_sha256'], locator='$.companies；按ticker定位',
            explanation='计算前资本数量级审阅；历史部分桥接不认证未来预算。')
        source_path(Evidence.model_validate(review))
        evidence.append(review)
        shared.append(review['id'])
        source = read_json(review_path)['source']
        evidence.append(dict(id='batch-capital-source', kind='reported_fact', file=source['path'],
            sha256=source['sha256'], locator=source['locator'], explanation='同版标准资本分量，保持各自范围。'))
        source_path(Evidence.model_validate(evidence[-1]))
        shared.append('batch-capital-source')
    stories, bases, seen = [], {}, set()
    for item in index['notes']:
        note_file = root/item['structured']
        if digest(note_file) != item['sha256'] or item['structured'] != relative_path(note_file):
            raise ValueError('分析师笔记路径或哈希不符')
        note = read_json(note_file)
        ticker = note['ticker']
        purpose = item.get('purpose', 'company_condition')
        if purpose not in ('company_condition', 'diagnostic_condition', 'deferred'):
            raise ValueError('研究执行用途须明确为公司条件、诊断条件或延期')
        adopted = ((note['analyst_status'] == ADOPTED and purpose != 'deferred')
                   or purpose == 'diagnostic_condition')
        reference = note['reference_snapshot']
        if (note.get('format') != NOTE or ticker != item['ticker'] or ticker in seen or ticker not in scope
                or note['source_sqlite_sha256'] != snapshot['sqlite_sha256']
                or note['financial_period'] != snapshot['financial_report_period']
                or (reference != snapshot['reference_snapshot_id'] and (adopted or reference is not None))):
            raise ValueError('分析师笔记格式、证券或消费版本不符：'+ticker)
        seen.add(ticker)
        if (root/item['note']).resolve(strict=True) != note_file.with_suffix('.md').resolve(strict=True):
            raise ValueError('研究正文与结构化笔记必须同名配对')
        relative_path(root/item['note'])
        key = ticker.lower().replace(':', '-')
        identifier, baseline_id = key+'-company-condition', key+'-baseline'
        native = read_report(baseline_rows[ticker]['report_id'])
        baseline_entry, baseline_file = verified_file(native, 'valuation')
        packet = read_json(baseline_file)
        if packet['financial_input_snapshot'] != snapshot['sqlite_sha256']:
            raise ValueError('原生基线属于不同消费版本：'+ticker)
        evidence.append(dict(id=baseline_id, kind='snapshot_baseline', file=baseline_entry.path,
            sha256=baseline_entry.sha256, locator='$.response', explanation='同版原生基线，仅作事实和旧条件对照。'))
        company_ids = []
        files = set()
        for source in [*note['source_snapshot'], *note['company_evidence']]:
            pair = (source['path'], source['sha256'])
            if pair in files:
                continue
            files.add(pair)
            source_id = key+'-company-source-'+str(len(company_ids))
            kind = ('snapshot_baseline' if source['path'] == baseline_entry.path else
                    source.get('kind') if source.get('kind') in ('reference', 'policy_proxy', 'research_judgment')
                    else 'reported_fact')
            entry = dict(id=source_id, kind=kind, file=source['path'], sha256=source['sha256'],
                locator=source.get('locator') or '本公司笔记明确引用的财务或经营披露',
                explanation='实际已读的本公司来源；哈希不替代语义审核，不作为未来事实。')
            source_path(Evidence.model_validate(entry))
            evidence.append(entry)
            company_ids.append(source_id)
        note_id = key+'-analyst-judgment'
        evidence.append(dict(id=note_id, kind='research_judgment', file=relative_path(note_file),
            sha256=item['sha256'], locator='$.thesis；$.parameter_basis；$.annual_assumptions；$.unresolved',
            explanation='计算前人工公司研究；未来数值是研究条件，不是公司承诺。'))
        prose_id = key+'-analyst-prose'
        evidence.append(dict(id=prose_id, kind='research_judgment', file=item['note'],
            sha256=digest(root/item['note']), locator='全文；与同名结构化笔记配对',
            explanation='计算前研究正文，与年度数值和反证共同冻结。'))
        limitations = note['unresolved'] or ['未来条件仍需经营兑现；没有认证预测有效性。']
        if purpose == 'diagnostic_condition':
            limitations = ['仅作诊断条件，尚未采纳为公司主估值；不得隐藏未解决的范围或融资条件。', *limitations]
        rationales = {entry['parameter']: entry['basis'] for entry in note.get('parameter_basis', [])}
        rationales['revenue'] = rationales.pop('growth', note['thesis']['growth_claim'])
        rationales.setdefault('margin', note['thesis']['margin_claim'])
        rationales.setdefault('capital', note['thesis']['capital_claim'])
        rationales.setdefault('research', '；'.join(limitations))
        rationales['tax'] = note.get('tax_and_research', {}).get('tax_basis') or '税务条件待完成。'
        rationales['terminal'] = note.get('terminal', {}).get('basis') or '终值条件待完成。'
        rationales['financing'] = ('沿同版人民币融资参考及已批准代理，不声称完成公司信用或期后资本结构认证。'
            + '；'.join(limitations))
        operating, overrides = authored_story(note, packet['response'], key) if adopted else ([], {})
        claims = []
        for topic in TOPICS:
            parameters = [topic]
            if topic == 'capital':
                parameters += [path for path in overrides if path.endswith(('annual_sales_to_capital', 'annual_net_reinvestment'))]
            if topic == 'terminal':
                parameters += [path for path in overrides if path.endswith(('roic_stable_override', 'stable_growth_rate'))]
            if topic == 'research' and 'valuation_assumptions.research_tax' in overrides:
                parameters.append('valuation_assumptions.research_tax')
            claims.append(dict(id=topic, statement=rationales[topic], parameters=parameters,
                evidence=[*company_ids, note_id, prose_id, *shared], evidence_state='insufficient',
                counterevidence=note['thesis']['counterevidence'],
                revision_trigger='新披露反证本公司需求、利润或投入机制时，明确修订对应年度条件。'))
        stories.append(dict(id=identifier, revision=1, ticker=ticker, baseline_evidence=baseline_id,
            title=('仅作诊断：' if purpose == 'diagnostic_condition' else '')+note['title'], narrative=note['business_and_competition'],
            economic_profile='逐公司人工研究条件；范围及重大边界另列', mechanisms=[note['thesis']['title']],
            status='conditional' if adopted else 'draft', claims=claims, annual_operating_plan=operating,
            assumption_overrides=overrides, limitations=limitations))
        bases[identifier] = {topic: dict(claim_ids=[topic], rationale=rationales[topic],
            decision=('unresolved' if not adopted else 'reference_proxy' if topic in ('financing', 'terminal')
                      else 'company_judgment'), limitations=limitations, unresolved=[]) for topic in TOPICS}
    plan = ResearchPlan.model_validate(dict(contract=CONTRACT, document=dict(contract='narrative-valuation-v2',
        snapshot=snapshot, scope=scope, evidence=evidence, stories=stories), basis=bases))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as stream:
        stream.write(json.dumps(plan.model_dump(mode='json'), ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    return dict(plan_file=relative_path(output), plan_sha256=digest(output), companies=len(scope),
        authored_companies=len(stories), source_index=relative_path(index_file), source_index_sha256=digest(index_file))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('index', type=Path)
    parser.add_argument('--baseline-report', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--database', type=Path, default=workspace_path('valuation.sqlite'))
    args = parser.parse_args()
    print(json.dumps(prepare_batch(args.index, args.baseline_report, args.output, args.database), ensure_ascii=False))


if __name__ == '__main__':
    main()
