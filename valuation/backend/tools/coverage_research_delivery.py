"""累计交付已核验的公司研究批次；不重算旧公司、不回退原生默认。"""
import argparse
from collections import Counter
from copy import deepcopy
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re

from data_sources.paths import workspace_path
from data_sources.research_reports import IDENTIFIER, publish, read_report, verified_file
from data_sources.valuation_results import ASSUMPTIONS, CONTRACT, result_path
from tools.coverage_research import ResearchPlan, assess_plan, relative_path
from tools.check_native_sqlite import value_digest
from tools.initial_coverage import save
from tools.initial_coverage_report import build_charts, model_review_flags
from tools.narrative_valuation import digest, read_json
from tools.valuation_results import import_snapshot

LIMITS = ['未来年度数值是逐公司研究条件，不是公司承诺、概率预测或当前目标价。',
          '财务、股本、现金及金融投资沿冻结消费快照，期后资本与参考代理限制保留。',
          '计算沿现有冻结引擎；不自动改变公司事实、网页默认或参数。']


def entry(identifier, label, path):
    return dict(id=identifier, label=label, path=relative_path(path), sha256=digest(path))


def merge_rows(previous, updates, scope):
    """完整分母不变；本批只能替换明确交付的公司，不改其他条件。"""
    rows = {row['ticker']: row for row in previous}
    if len(rows) != len(previous) or set(rows) != set(scope):
        raise ValueError('累计研究必须保留完整且唯一的发布分母')
    if len({row['ticker'] for row in updates}) != len(updates):
        raise ValueError('本批研究重复证券')
    for row in updates:
        if row['ticker'] not in rows:
            raise ValueError('本批公司不属于完整发布范围')
        rows[row['ticker']] = row
    return list(rows.values())


def verify_model(model, result, run, protocol, expected_request):
    if (model.get('contract') != 'coverage-company-research-result-v1'
            or model.get('ticker') != result['ticker'] or model.get('story_id') != result['story_id']
            or model.get('run_id') != run['run_id'] or model.get('snapshot') != run['snapshot']
            or model.get('frozen_sha256') != protocol['frozen_sha256']
            or model.get('request_sha256') != expected_request
            or value_digest(model['request']) != expected_request
            or model['response']['final']['value_per_share'] != result['value_per_share']):
        raise ValueError('公司模型、实际请求或结果与冻结运行不一致')


def price_context(native, note):
    result = {key: native[key] for key in ('price_comparison_status', 'price_comparison_reason')}
    context = note.get('price_comparison')
    if context:
        if context.get('status') != 'unreconciled_post_period_capital_change' or not context.get('reason'):
            raise ValueError('研究报价边界只能明确保留已知未桥接资本变化，不能自动放开比较')
        result.update(price_comparison_status=context['status'], price_comparison_reason=context['reason'])
    return result


def condition_row(source, result, model, report, frozen_at):
    row = deepcopy(source)
    calculated = model is not None
    row.update(admission='ready' if calculated else source['admission'] if source['admission'] not in ('ready', 'not_calculated') else 'not_calculated',
        reason='公司研究条件已冻结并计算' if calculated else '已研究，重要边界待闭合；不以原生默认替代',
        report_id=report.id, url='/research/'+report.id,
        mode='authored_company_condition' if calculated else 'company_research_pending',
        condition_value_cny_per_share=result['value_per_share'] if calculated else None,
        share_date=report.share_date.isoformat() if report.share_date else None,
        research_frozen_at=frozen_at, run_id=model['run_id'] if calculated else None,
        descriptive_difference_pct=None, assumptions=dict.fromkeys(ASSUMPTIONS), review_flags=[],
        model_source=dict(path=None, sha256=None))
    if calculated:
        body, review = model['response'], model['review']
        first, terminal = review['forecast'][0], review['terminal']
        row['assumptions'].update(initial_wacc_rate=body['cost_of_capital']['wacc'],
            first_year_growth_rate=first['growth'], first_year_adjusted_margin_rate=first['adjusted_operating_margin'],
            first_year_funding_sales_to_capital=first['capital_funding'].get('sales_to_capital'),
            terminal_growth_rate=terminal['growth'], terminal_wacc_rate=terminal['wacc'],
            terminal_pv_share_rate=terminal['value_share'])
        row['review_flags'] = model_review_flags({**model, 'admission': 'ready'})
        locator = next(file for file in report.files if file.id == 'valuation')
        row['model_source'] = dict(path=locator.path, sha256=locator.sha256)
        if source['price_comparison_status'] == 'capital_date_proxy' and (source['close_cny'] or 0) > 0:
            row['descriptive_difference_pct'] = (result['value_per_share']/source['close_cny']-1)*100
    row['comparison_display_status'] = source['price_comparison_status'] if calculated else 'not_calculated'
    row['comparison_display_reason'] = source['price_comparison_reason'] if calculated else '未生成新研究值，不比较价差'
    if not calculated:
        row.update(price_comparison_status='not_calculated', price_comparison_reason=row['comparison_display_reason'])
    row['assumption_missing_reasons'] = {key: ('显式总净预算，资本倍率不适用' if calculated else '本轮尚未生成研究值')
        for key, value in row['assumptions'].items() if value is None}
    return row


def company_report(identifier, source, result, story, evidence, plan, frozen, run, note_file):
    root = workspace_path()
    folder = root/'research'/identifier
    note, note_text = read_json(note_file), note_file.with_suffix('.md')
    model = read_json(root/result['model_file']) if result['status'] == 'calculated_company_condition' else None
    if (folder/'research.json').exists():
        report = read_report(identifier)
        for field, expected in [('authored-note', digest(note_file)), ('authored-text', digest(note_text)),
                                *([('valuation', result['model_sha256'])] if model else [])]:
            existing, _ = verified_file(report, field)
            if existing.sha256 != expected:
                raise ValueError('已登记公司报告内容不同，须使用新ID：'+identifier)
        if report.tickers != [source['ticker']] or report.run_ids != ([run['run_id']] if model else []):
            raise ValueError('已登记公司报告身份或运行不一致')
        return report, model
    if folder.exists() and any(folder.iterdir()):
        raise ValueError('发现未登记的非空公司目录；核对失败产物后再恢复：'+identifier)
    folder.mkdir(exist_ok=True)
    files = [entry('authored-note', '计算前的结构化公司研究', note_file), entry('authored-text', '计算前研究正文', note_text)]
    text = ['# '+source['name']+'：公司故事与'+('条件估值' if model else '研究边界'), '',
        f"证券：`{source['ticker']}`；财务／股本基期{run['snapshot']['financial_report_period']}；信息截止`{run['snapshot']['information_as_of']}`。", '',
        '未来数值是阅读本公司资料后明确采用的研究条件，不是公司承诺。原生默认只作基线，不代替公司故事。', '']
    text += ['报价与资本比较边界：'+source['price_comparison_reason']+'；不以较新研究日期伪装已更新股本。', '']
    if 'batch-capital-review' in evidence:
        capital = read_json(root/evidence['batch-capital-review'].file)
        reviewed = next(row for row in capital['companies'] if row['ticker'] == source['ticker'])
        text += ['## 计算前资本数量级复核', '', reviewed['assessment'], '']
    if model:
        if digest(root/result['model_file']) != result['model_sha256'] or model['snapshot'] != run['snapshot']:
            raise ValueError('本批模型或快照已变化')
        body, review = model['response'], model['review']
        text += [f"**每股条件值：{result['value_per_share']:,.2f}元人民币**；原生基线{result['baseline_value_per_share']:,.2f}元。", '',
            f"运行`{model['run_id']}`；实际请求哈希`{model['request_sha256']}`。", '',
            '## 故事形成的年度现金流', '',
            '| 滚动年 | 收入／亿元 | 调整利润率 | 税率 | NOPAT／亿元 | 总净再投资／亿元 | FCFF／亿元 |',
            '|---|---:|---:|---:|---:|---:|---:|']
        for year in review['forecast']:
            text.append(f"| {year['year']} | {year['revenue_million_cny']/100:.2f} | {year['adjusted_operating_margin']:.2%} | {year['tax_base_rate']:.2%} | {year['nopat_million_cny']/100:.2f} | {year['reinvestment_million_cny']/100:.2f} | {year['fcff_million_cny']/100:.2f} |")
        terminal, transition = review['terminal'], review['economic_checks']['terminal_transition']
        text += ['', f"初始WACC {body['cost_of_capital']['wacc']:.2%}；终值g {terminal['growth']:.2%}；终值WACC {terminal['wacc']:.2%}；终值现值占比{terminal['value_share']:.2%}。", '',
            '### 终值、资本与融资边界', '',
            f"末年总净投入{review['forecast'][-1]['reinvestment_million_cny']/100:.2f}亿元；终值首年按g/ROIC投入{transition['reinvestment_million_cny']/100:.2f}亿元。与同一终值年度继续原资本规则相比，投入改变{transition['reinvestment_rule_change_million_cny']/100:+.2f}亿元，FCFF改变{transition['fcff_rule_change_million_cny']/100:+.2f}亿元。", '',
            '终点是另行声明的研究条件；较低永续增长率或ROIC随WACC不证明第十年已平稳达到稳态。资本跳变不是已经出售资产，不为消除提示偷偷改算法。', '']
        cumulative, trough = 0., 0.
        for year in review['forecast']:
            cumulative += year['fcff_million_cny']
            trough = min(trough, cumulative)
        if trough < 0:
            text += [f"累计融资前FCFF最低点{trough/100:.2f}亿元；加回现金代理{body['dcf']['equity_bridge_components']['cash']/100:.2f}亿元。FCFF不是银行余额；尚须债务到期、必要经营现金及可取得融资支持，不能以低账面债务证明融资充裕，也不重复从股权价值扣投入。", '']
        if 'capital_release_requires_recoverability_basis' in model_review_flags({**model, 'admission': 'ready'}):
            text += ['资本释放提示同时包括总净投入及非研发分量，模型回收须有周转／资产处置依据，不是已实现现金。', '']
        os.link(root/result['model_file'], folder/'valuation.json')
        native = read_report(source['native_report_id'])
        _, fact_file = verified_file(native, 'facts')
        _, business_file = verified_file(native, 'business-evidence')
        os.link(fact_file, folder/'facts.json')
        facts, business = read_json(fact_file), read_json(business_file)
        business['report_end'] = run['snapshot']['financial_report_period']
        charts = build_charts(folder, source, {**model, 'sensitivities': []}, facts['source_periods'], business, business.get('source'))
        files += [entry('valuation', '冻结输入、实际结果与现金流审阅', folder/'valuation.json'),
                  entry('facts', '同版标准财务研究事实', folder/'facts.json'),
                  entry('chart-data', '精确图表数组与来源', folder/'charts/chart-data.json'),
                  entry('chart-manifest', '图表版本及哈希', folder/'charts/manifest.json')]
        takeaways = {chart['id']: chart['takeaway'] for chart in read_json(folder/'charts/chart-data.json')['charts']}
        for picture in charts['files']:
            files.append(entry('chart-'+picture['id'], picture['label'], root/picture['path']))
            text += [f"![{picture['label']}](charts/{Path(picture['path']).name})", '', takeaways[picture['id']], '']
        text += ['## 各项条件的采用依据', '']
        for topic, basis in plan.basis[story.id].items():
            text.append('- **'+topic+'**：'+basis.rationale)
    else:
        text += ['**已完成本公司资料研究，但重要范围／口径或条件仍待解决，本轮不编造新的每股值。**', '',
            '；'.join(note['unresolved']), '']
    text += ['', '## 计算前的公司研究', '',
        '下文保持计算前判断，执行状态以本页上方为准。', '', note_text.read_text(), '',
        '## 来源与原生基线', '', f"[原生资料与基线](/research/{source['native_report_id']})；计算通过不认证预测有效性。"]
    seen = {file['path'] for file in files}
    for claim in story.claims:
        for key in claim.evidence:
            source_evidence = evidence[key]
            if source_evidence.file not in seen:
                seen.add(source_evidence.file)
                files.append(entry('source-'+str(len(files)), source_evidence.explanation, root/source_evidence.file))
    (folder/'report.md').write_text('\n'.join(text)+'\n')
    now = datetime.now(timezone.utc)
    report = publish(folder, dict(contract='alphalake-research-v1', id=identifier,
        title=source['name']+'：'+(f"公司研究条件值{result['value_per_share']:.2f}元" if model else '公司研究边界'),
        summary='业务、竞争、财务质量与资本条件均单独研究；数值成立条件及反证见正文。',
        kind='initial_coverage' if model else 'research_note', status='complete' if model else 'partial',
        tickers=[source['ticker']], research_date=now.date().isoformat(),
        financial_period=run['snapshot']['financial_report_period'], information_as_of=now.isoformat(),
        share_date=run['snapshot']['financial_report_period'] if model else None,
        run_ids=[run['run_id']] if model else [], previous_id=source['report_id'], limitations=LIMITS, files=[
            entry('report', '公司故事、年度条件与实际估值', folder/'report.md'), *files]))
    return report, model


def deliver(run_directory, previous_dataset, baseline_report, identifier):
    if not re.fullmatch(IDENTIFIER, identifier):
        raise ValueError('累计报告须使用规范ID，不能包含目录路径')
    root = workspace_path()
    run_directory = Path(run_directory)
    run, protocol = read_json(run_directory/'summary.json'), read_json(run_directory/'protocol.json')
    frozen_file = root/protocol['frozen_file']
    frozen = read_json(frozen_file)
    plan = ResearchPlan.model_validate(read_json(root/frozen['plan_file']))
    if digest(frozen_file) != protocol['frozen_sha256'] or protocol['run_id'] != run['run_id']:
        raise ValueError('运行协议与冻结收据不符')
    assessment = assess_plan(frozen_file, expected_scope=plan.document.scope, expected_snapshot=run['snapshot'])
    requests = {row['id']: row['request_sha256'] for row in assessment['stories']}
    previous = read_json(result_path(previous_dataset, 'index.json'))
    for field in ('sqlite_sha256', 'reference_snapshot_id'):
        if previous['dataset'][field] != run['snapshot'][field]:
            raise ValueError('累计研究不允许混接财务／参考快照')
    for stage in ('calculation', 'review'):
        if previous['dataset']['runtime_stages'][stage] != protocol['runtime']['stages'][stage]:
            raise ValueError('累计研究计算或审阅方法版本不一致，须明确重建')
    old_report = read_report(previous['dataset']['source_report_id'])
    _, old_results_file = verified_file(old_report, 'results')
    old_results = read_json(old_results_file)
    reviewed = set(old_results.get('reviewed_tickers') or [row['ticker'] for row in previous['rows']
        if row['report_id'] != old_report.id])
    native = read_report(baseline_report)
    _, native_results_file = verified_file(native, 'results')
    native_rows = {row['ticker']: row for row in read_json(native_results_file)['results']}
    previous_rows = {row['ticker']: row for row in previous['rows']}
    evidence = {item.id: item for item in plan.document.evidence}
    stories = {story.id: story for story in plan.document.stories}
    updates, new_reports = [], []
    if reviewed & {row['ticker'] for row in run['results']}:
        raise ValueError('本批重复已研究公司；修订须显式新建公司版本，不当作新增覆盖')
    for result in run['results']:
        if result['status'] == 'not_selected':
            continue
        ticker, story = result['ticker'], stories[result['story_id']]
        if result['model_file']:
            model_file = root/result['model_file']
            if digest(model_file) != result['model_sha256']:
                raise ValueError('公司模型文件哈希不符')
            verify_model(read_json(model_file), result, run, protocol, requests[story.id])
        note_id = ticker.lower().replace(':', '-')+'-analyst-judgment'
        if note_id not in evidence or evidence[note_id].kind != 'research_judgment':
            raise ValueError('每家公司须引用明确计算前研究笔记')
        comparison = price_context(native_rows[ticker], read_json(root/evidence[note_id].file))
        source = {**previous_rows[ticker], **comparison, 'report_id': native_rows[ticker]['report_id'],
            'native_report_id': native_rows[ticker]['report_id']}
        company_id = identifier+'-'+ticker.lower().replace('shse:', 'sh-').replace('szse:', 'sz-')
        report, model = company_report(company_id, source, result, story, evidence, plan,
            frozen, run, root/evidence[note_id].file)
        updates.append(condition_row(previous_rows[ticker] | comparison, result, model, report, frozen['frozen_at']))
        new_reports.append(report.id)
        reviewed.add(ticker)
    rows = merge_rows(previous['rows'], updates, plan.document.scope)
    folder = root/'research'/identifier
    if folder.exists():
        raise ValueError('累计报告不可覆盖')
    folder.mkdir()
    ready = sum(row['admission'] == 'ready' for row in rows)
    whole_complete = all(ticker in reviewed or scope == 'outside_financial_scope'
        for ticker, scope in plan.document.scope.items())
    summary = dict(contract='company-research-delivery-v2', companies=len(rows), actual_notes=len(reviewed),
        reviewed_tickers=sorted(reviewed), authored_conditions=ready, snapshot=run['snapshot'], rows=rows,
        new_reports=new_reports, new_run=relative_path(run_directory/'summary.json'),
        previous_report_id=old_report.id, whole_market_research_complete=whole_complete)
    save(folder/'results.json', summary)
    with (folder/'results.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, ['ticker', 'name', 'admission', 'condition_value_cny_per_share',
            'close_cny', 'descriptive_difference_pct', 'reason', 'url'], extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    text = ['# 全范围公司故事与条件估值', '',
        f"完整{len(rows):,}家公司；累计实际研究{len(reviewed):,}家，已计算公司条件{ready:,}家。全范围研究{'已逐家公司处理' if whole_complete else '仍在持续推进，尚未完成'}。", '',
        '未进入本批不撤销既有公司条件；未研究不以原生默认冒充。金融范围外、缺数据和未解决经济边界保留。', '',
        '## 本批交付', '', *['- ['+report_id+'](/research/'+report_id+')' for report_id in new_reports], '',
        '[完整结果与状态](results.csv)；全部公司及旧研究入口见选股页。算术核验与经济依据、未来预测有效性分开。']
    (folder/'report.md').write_text('\n'.join(text)+'\n')
    now = datetime.now(timezone.utc)
    report = publish(folder, dict(contract='alphalake-research-v1', id=identifier,
        title=f"公司故事：{len(reviewed):,}家已研究／{ready:,}家已计算，完整{len(rows):,}家",
        summary='持续按固定完整队列逐公司研究；保留既有已核验条件，不重算旧公司，不回退原生默认。',
        kind='narrative_batch', status='complete' if whole_complete else 'partial', tickers=[],
        research_date=now.date().isoformat(), financial_period=run['snapshot']['financial_report_period'],
        information_as_of=now.isoformat(), run_ids=[run['run_id']], previous_id=old_report.id,
        limitations=LIMITS+['范围处理完成不等于所有公司均获得可采用的条件值。'], files=[
            entry('report', '累计进度及本批公司入口', folder/'report.md'),
            entry('results', '完整公司范围与逐公司状态', folder/'results.json'),
            entry('csv', '完整结果表格', folder/'results.csv'),
            entry('plan', '本批公司研究与完整冻结范围', root/frozen['plan_file']),
            entry('frozen', '计算前来源与实际请求哈希', frozen_file),
            entry('run', '本批实际API计算摘要', run_directory/'summary.json')]))
    metadata = {**previous['dataset'], 'id': identifier, 'title': report.title,
        'source_report_id': identifier, 'report_url': '/research/'+identifier,
        'csv_url': f'/api/research/{identifier}/files/csv?download=true', 'research_date': now.date().isoformat(),
        'research_frozen_at': None, 'built_at': now.isoformat(), 'build_version': 'company-research-delivery-v2',
        'condition_type': '逐公司人工研究条件；各公司沿各自冻结时间与run ID，不代表同一条经营路径',
        'limitations': report.limitations, 'share_dates': dict(Counter(row['share_date'] or 'unknown' for row in rows)),
        'quote_dates': dict(Counter(row['close_date'] or 'unknown' for row in rows)),
        'source_result_sha256': digest(folder/'results.json'), 'source_manifest_sha256': digest(folder/'research.json'),
        'source_files': [dict(report_id=identifier, file_id='results', sha256=digest(folder/'results.json'))]}
    snapshot = dict(contract=CONTRACT, dataset=metadata, universe_count=len(rows), rows=rows,
        admission_counts=dict(Counter(row['admission'] for row in rows)),
        review_flag_counts=dict(Counter(flag for row in rows for flag in row['review_flags'])), issues=[])
    screen_file = run_directory/'screen.json'
    save(screen_file, snapshot)
    return dict(report_id=identifier, actual_notes=len(reviewed), authored_conditions=ready,
        new_company_reports=len(updates), screen=import_snapshot(screen_file, activate=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_directory', type=Path)
    parser.add_argument('--previous-dataset', required=True)
    parser.add_argument('--baseline-report', required=True)
    parser.add_argument('--id', required=True)
    args = parser.parse_args()
    print(json.dumps(deliver(args.run_directory, args.previous_dataset, args.baseline_report, args.id), ensure_ascii=False))


if __name__ == '__main__':
    main()
