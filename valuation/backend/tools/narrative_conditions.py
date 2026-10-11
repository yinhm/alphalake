"""按人工指定的历史锚点展开故事年度条件；不识别公司或选择经济参数。"""
import argparse
from collections import Counter
from copy import deepcopy
import csv
from datetime import date
from html import escape
import json
import os
from pathlib import Path
import sqlite3

from pydantic import Field, model_validator

from engine.data_dictionary import CompanyValuationInput
from engine.module_1_adjustments import capitalize_r_and_d
from tools.cyclical_valuation import cycle_conditions
from tools.evaluate_native_policy import research_tax_condition
from tools.narrative_valuation import Document, Record, digest, read_json, save, source_path


class Recipe(Record):
    id: str = Field(pattern=r'^[a-z0-9][a-z0-9-]*$')
    title: str = Field(min_length=1)
    narrative: str = Field(min_length=1)
    growth_anchor: str
    hold_years: int = Field(ge=1, le=9)
    stable_year: int = Field(ge=2, le=10)
    margin_anchor: str
    margin_year: int = Field(ge=1, le=10)
    cycle_variant: str | None = None

    @model_validator(mode='after')
    def anchors(self):
        if self.growth_anchor not in ('latest_fy', 'three_year_cagr', 'flat', 'existing_cycle'):
            raise ValueError('unknown explicit growth anchor')
        if self.margin_anchor not in ('current_reported', 'three_year_weighted', 'existing_cycle'):
            raise ValueError('unknown explicit margin anchor')
        if self.stable_year <= self.hold_years:
            raise ValueError('stable year must follow held growth years')
        if (self.growth_anchor == 'existing_cycle') != bool(self.cycle_variant):
            raise ValueError('explicit existing cycle variant required')
        if (self.margin_anchor == 'existing_cycle') != bool(self.cycle_variant):
            raise ValueError('cycle anchors must use the same existing condition')
        if self.cycle_variant not in (None, 'arithmetic-3y', 'revenue_weighted-3y'):
            raise ValueError('unsupported existing cycle variant')
        return self


def operating_condition(baseline, recipe):
    """历史是观测；向未来延续及过渡年数是调用者显式条件。"""
    if recipe.cycle_variant:
        variants, evidence = cycle_conditions(baseline)
        row = variants[recipe.cycle_variant]
        if row['status'] != 'prepared_explicit_condition':
            raise ValueError(row['reason'])
        request = row['request']['inputs']
        annual = request['valuation_assumptions']['annual_forecast']
        overrides = {'valuation_assumptions.annual_sales_to_capital': request['valuation_assumptions']['annual_sales_to_capital']}
        if request['valuation_assumptions']['research_tax'] is not None:
            overrides['valuation_assumptions.research_tax'] = request['valuation_assumptions']['research_tax']
        revenue, plan = baseline['ltm_financials']['revenues'], []
        for i, row in enumerate(annual):
            revenue *= 1+row['growth']
            plan.append(dict(year=i+1, revenue_million_cny=revenue,
                adjusted_operating_margin=row['margin'], tax_rate=row['tax']))
        return plan, overrides, dict(recipe=recipe.model_dump(), cycle=evidence,
            target_reported_margin=variants[recipe.cycle_variant]['target_reported_margin'])
    inputs = CompanyValuationInput.model_validate(baseline['inputs'])
    a, adj = inputs.valuation_assumptions, inputs.adjustment_inputs
    if a.projection_years != 10 or any(v is not None for v in
            (a.annual_forecast, a.annual_net_reinvestment, a.annual_sales_to_capital, a.research_tax)):
        raise ValueError('ten-year baseline without an existing explicit future path required')
    if adj.has_operating_leases:
        raise ValueError('reported margin requires matching historical lease scope')
    revenue0, ebit0 = baseline['ltm_financials']['revenues'], baseline['ltm_financials']['ebit']
    if revenue0 is None or revenue0 <= 0 or ebit0 is None:
        raise ValueError('positive base revenue and reported EBIT required')
    year = date.fromisoformat(inputs.period_date_10k[:10]).year
    annual = {}
    for row in inputs.raw_financials:
        if year-3 <= row.fiscal_year <= year:
            if row.fiscal_year in annual:
                raise ValueError('ambiguous annual history')
            annual[row.fiscal_year] = row
    required = set()
    if recipe.growth_anchor == 'latest_fy':
        required |= {year-1, year}
    elif recipe.growth_anchor == 'three_year_cagr':
        required |= set(range(year-3, year+1))
    if recipe.margin_anchor == 'three_year_weighted':
        required |= set(range(year-2, year+1))
    if any(y not in annual or annual[y].revenues is None or annual[y].revenues <= 0 for y in required):
        raise ValueError('complete positive annual revenue anchors required: '+str(sorted(required)))
    if recipe.growth_anchor == 'latest_fy':
        growth = annual[year].revenues/annual[year-1].revenues-1
    elif recipe.growth_anchor == 'three_year_cagr':
        growth = (annual[year].revenues/annual[year-3].revenues)**(1/3)-1
    else:
        growth = 0.
    if not -1 < growth <= 1:
        raise ValueError('observed growth outside native bounds; not clipped: '+str(growth))
    current_margin = ebit0/revenue0
    if recipe.margin_anchor == 'three_year_weighted':
        rows = [annual[y] for y in range(year-2, year+1)]
        if any(r.ebit is None for r in rows):
            raise ValueError('complete reported EBIT anchors required')
        target = sum(r.ebit for r in rows)/sum(r.revenues for r in rows)
    else:
        target = current_margin
    if target <= 0:
        raise ValueError('nonpositive terminal reported margin; explicit recovery or finite-life story required')
    g = baseline['dcf']['terminal_growth']
    growth_path = [growth+(g-growth)*min(max(i-recipe.hold_years, 0)/
        (recipe.stable_year-recipe.hold_years), 1) for i in range(1, 11)]
    revenues, value = [], revenue0
    for rate in growth_path:
        value *= 1+rate
        revenues.append(value)
    reported = [current_margin+(target-current_margin)*min(i/recipe.margin_year, 1) for i in range(1, 11)]
    overrides, amortization, expenses = {}, [0.]*10, [0.]*10
    if adj.has_r_and_d:
        changed = deepcopy(baseline)
        changed['dcf']['revenue_projections'] = revenues
        request, rd = research_tax_condition(changed, g)
        research = request['inputs']['valuation_assumptions']['research_tax']
        overrides['valuation_assumptions.research_tax'] = research
        expenses = research['expenses']
        life = adj.amortization_period_n
        cohorts = [adj.r_and_d_expense_current, *adj.r_and_d_expense_past[:life-1]]
        if len(cohorts) != life or any(v is None or v < 0 for v in cohorts):
            raise ValueError('complete original research cohort required; no estimation')
        amortization = []
        for expense in expenses:
            _, amort, _ = capitalize_r_and_d(expense, cohorts, life)
            amortization.append(amort)
            cohorts = [expense, *cohorts[:life-1]]
    else:
        rd = None
    plan = [dict(year=i+1, revenue_million_cny=r, adjusted_operating_margin=m+(e-d)/r, tax_rate=t)
        for i, (r, m, e, d, t) in enumerate(zip(revenues, reported, expenses, amortization,
            baseline['dcf']['tax_rate_projections'], strict=True))]
    if any(not -1 <= row['adjusted_operating_margin'] <= 1 for row in plan):
        raise ValueError('adjusted margin outside native bounds; not clipped')
    return plan, overrides, dict(recipe=recipe.model_dump(), historical_anchor_years=sorted(required),
        observed_growth=growth, current_reported_margin=current_margin, target_reported_margin=target,
        terminal_growth=g, research=rd, growth_is_forecast_fact=False,
        history=[annual[y].model_dump(mode='json') for y in sorted(required)])


def build(prepared_file, assignment_file, output):
    from data_sources.paths import workspace_path
    if not output.resolve().is_relative_to(workspace_path('derived').resolve()):
        raise ValueError('output under workspace/derived required')
    if output.exists():
        raise ValueError('immutable output already exists')
    document = Document.model_validate(read_json(prepared_file))
    assignment = read_json(assignment_file)
    if assignment['contract'] != 'explicit-narrative-assignments-v1' or assignment['automatic_adoption']:
        raise ValueError('explicit unadopted assignment contract required')
    records = assignment['companies']
    if len({r['ticker'] for r in records}) != len(records) or {r['ticker'] for r in records} != set(document.scope):
        raise ValueError('retain exact prepared company denominator')
    registered = {e.id:e for e in document.evidence}
    for evidence in assignment['evidence']:
        from tools.narrative_valuation import Evidence
        item = Evidence.model_validate(evidence)
        source_path(item)
        if item.id in registered:
            raise ValueError('duplicate assigned evidence')
        document.evidence.append(item)
    document.evidence.append(dict_to_evidence(assignment_file, workspace_path()))
    # Explicit assignments are data, not an industry classifier or parameter selector.
    drafts = {s.ticker:s for s in document.stories}
    stories, anchors = [], []
    for record in records:
        original = drafts[record['ticker']]
        baseline = read_json(source_path(registered[original.baseline_evidence]))
        recipes = record['recipes'] if document.scope[original.ticker] == 'nonfinancial_by_reference' else []
        if not recipes:
            recipes = [None]
        for raw in recipes:
            story = original.model_dump(mode='json')
            story.update(economic_profile=record['profile'], mechanisms=record['mechanisms'],
                narrative=record['narrative'], title=record['title'], limitations=record['limitations'])
            evidence = [original.baseline_evidence, 'explicit-assignments', *record['evidence']]
            anchor = dict(ticker=original.ticker, profile=record['profile'], assigned_before_results=True)
            if raw is not None:
                recipe = Recipe.model_validate(raw)
                story.update(id=original.ticker.lower().replace(':', '-')+'-'+recipe.id,
                    title=record['title']+'：'+recipe.title, narrative=record['narrative']+' '+recipe.narrative)
                try:
                    plan, overrides, values = operating_condition(baseline['body'], recipe) if baseline['http_status'] == 200 else ([], {}, {})
                    story.update(status='conditional' if plan else 'draft', annual_operating_plan=plan, assumption_overrides=overrides)
                    anchor.update(values)
                except ValueError as error:
                    story.update(status='not_applicable', annual_operating_plan=[], assumption_overrides={})
                    story['limitations'] = [*story['limitations'], str(error)]
                    anchor['rejection'] = str(error)
            else:
                story.update(status='not_applicable', annual_operating_plan=[], assumption_overrides={})
            story['claims'] = [dict(id='operating', statement=story['narrative'],
                parameters=['revenue', 'margin', 'tax'], evidence=evidence, evidence_state='insufficient',
                counterevidence=record['counterevidence'], revision_trigger=record['revision_trigger']),
                dict(id='resources', statement=record['resource_basis'],
                parameters=['capital', 'research', 'financing', 'terminal', *story['assumption_overrides']],
                evidence=evidence, evidence_state='insufficient', counterevidence=record['resource_counterevidence'],
                revision_trigger=record['revision_trigger'])]
            stories.append(story)
            anchors.append(dict(story_id=story['id'], **anchor))
    payload = document.model_dump(mode='json')
    payload['stories'] = stories
    document = Document.model_validate(payload)
    save(output, document.model_dump(mode='json'))
    save(output.with_suffix('.anchors.json'), anchors)
    return dict(companies=len(document.scope), stories=len(stories), file=str(output), sha256=digest(output))


def dict_to_evidence(path, workspace):
    from tools.narrative_valuation import Evidence
    return Evidence(id='explicit-assignments', kind='research_judgment',
        file=str(path.resolve().relative_to(workspace.resolve())), sha256=digest(path),
        locator='逐公司画像、因果机制、显式历史锚点、过渡年数、反例及停止条件',
        explanation='人工研究条件；不认证公司计划、经济合理参数选择或发生概率')


def report(run_dir, database, output):
    """从已核验故事响应及同版报价生成完整证券表；不选择展示胜出者。"""
    from openpyxl import Workbook
    from tools.csi300_valuation import comparison
    from tools.narrative_valuation import new_output, verify
    verified = verify(run_dir)
    document = Document.model_validate(read_json(run_dir/'stories.json'))
    if digest(database) != document.snapshot['sqlite_sha256']:
        raise ValueError('report quote snapshot differs from narrative inputs')
    assignment = read_json(run_dir/'evidence'/'explicit-assignments.json')
    companies = assignment['companies']
    if len({r['ticker'] for r in companies}) != len(companies) or {r['ticker'] for r in companies} != set(document.scope):
        raise ValueError('report retains exact assigned company denominator')
    with sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True) as conn:
        from data_sources.us_cn_hk_db import input_contexts
        quotes = {r['ticker'].split(':')[-1]:r['market'].get('quote') or {} for r in input_contexts(conn)}
        prices = dict(conn.execute('SELECT ticker,stock_price_listing FROM companies'))
    results = read_json(run_dir/'summary.json')['results']
    stories = {s.id:s for s in document.stories}
    rows = []
    for company in companies:
        ticker = company['ticker']
        quote, price = quotes[company['code']], prices[ticker]
        if quote and (quote['adjustment'] != 'unadjusted' or float(quote['close']) != price):
            raise ValueError('listing price/quote evidence mismatch')
        row = dict(ticker=ticker, code=company['code'], name=company['name'], industry=company['industry'],
            profile=company['profile'], narrative=company['narrative'], scope=document.scope[ticker],
            price=price, price_date=quote.get('trade_date'), stories=[], baseline_value=None)
        for result in (r for r in results if r['ticker'] == ticker):
            story = stories[result['story_id']]
            item = dict(result, narrative=story.narrative, counterevidence=company['counterevidence'],
                revision_trigger=company['revision_trigger'], limitations=story.limitations)
            if result['status'] == 'calculated_conditional':
                body = read_json(run_dir/(story.id+'-result.json'))
                if body['ltm_financials']['stock_price'] != price:
                    raise ValueError('story response/listing quote mismatch')
                review = read_json(run_dir/(story.id+'-review.json'))
                item['nonresearch_capital_release_years'] = [r['year'] for r in review['forecast']
                    if r.get('implied_nonresearch_net_reinvestment_million_cny', 0) < -1e-8]
                item['terminal_transition_requires_basis'] = review['economic_checks']['terminal_transition']['requires_capital_transition_basis']
                item['difference_yuan'], item['difference_pct'] = comparison(item['value_per_share'], price)
                item['year_one_revenue_growth'] = body['dcf']['revenue_projections'][0]/body['ltm_financials']['revenues']-1
                item['year_one_adjusted_margin'] = body['dcf']['ebit_projections'][0]/body['dcf']['revenue_projections'][0]
                item['wacc'] = body['cost_of_capital']['wacc']
            row['baseline_value'] = result.get('baseline_value_per_share', row['baseline_value'])
            row['stories'].append(item)
        row['calculated'] = any(s['status'] == 'calculated_conditional' for s in row['stories'])
        rows.append(row)
    if digest(database) != document.snapshot['sqlite_sha256']:
        raise ValueError('quote snapshot changed during report')
    output = new_output(output)
    summary = dict(contract='narrative-company-price-comparison-v1', run_id=verified['run_id'],
        companies=len(rows), companies_with_values=sum(r['calculated'] for r in rows),
        conditions=verified['calculated'], scope_counts=dict(Counter(r['scope'] for r in rows)),
        statuses=dict(Counter(s['status'] for r in rows for s in r['stories'])),
        financial_period=document.snapshot['metadata']['report_period'],
        information_as_of=document.snapshot['metadata']['information_as_of'],
        price_dates=dict(Counter(r['price_date'] for r in rows)),
        source_sqlite_sha256=document.snapshot['sqlite_sha256'], selected_story=None,
        economic_forecasts_verified=False, automatic_adoption=False, results=rows)
    save(output/'results.json', summary)
    headers = ['代码', '名称', '申万细分', '经济画像', '故事', '收盘价', '报价日', '原默认条件值']
    for label in ('A', 'B'):
        headers += [label+'故事', label+'状态', label+'条件每股值', label+'价差元', label+'价差百分比', label+'边界／原因']
    cells = []
    for row in rows:
        values = [row[k] for k in ('code', 'name', 'industry', 'profile', 'narrative', 'price', 'price_date', 'baseline_value')]
        for i in range(2):
            story = row['stories'][i] if len(row['stories']) > i else {}
            reason = story.get('reason', '')
            if story.get('nonresearch_capital_release_years'):
                reason += '；隐含非研发资本释放年='+str(story['nonresearch_capital_release_years'])
            if story.get('terminal_transition_requires_basis'):
                reason += '；末期／终值资本转换依据待核实'
            values += [story.get(k) for k in ('title', 'status', 'value_per_share', 'difference_yuan', 'difference_pct')]+[reason]
        cells.append(values)
    with (output/'results.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream); writer.writerow(headers); writer.writerows(cells)
    book = Workbook(); sheet = book.active; sheet.title = '故事及价差'; sheet.append(headers)
    for values in cells:
        sheet.append(values)
        for cell in sheet[sheet.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = 's'
    sheet.freeze_panes = 'C2'; sheet.auto_filter.ref = sheet.dimensions
    book.save(output/'results.xlsx')
    def cell(value):
        return '—' if value is None else f'{value:.2f}' if isinstance(value, float) else str(value)
    note = (f"完整{len(rows)}只、{summary['companies_with_values']}家公司有条件值、{summary['conditions']}条条件。"
        f"财务／股本{summary['financial_period']}，截止{summary['information_as_of']}。"
        '金额人民币元；A/B按冻结输入顺序并列，没有概率或胜出者。画像、年数及历史延续是研究条件，资本／融资依据未全部闭合；不是目标价。')
    table = '<tr>'+''.join('<th>'+escape(h)+'</th>' for h in headers)+'</tr>'
    for values in cells:
        table += '<tr>'+''.join('<td>'+escape(cell(v))+'</td>' for v in values)+'</tr>'
    detail_link = escape(os.path.relpath(run_dir/'report.md', output), quote=True)
    (output/'results.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>故事与条件估值</title>'
        '<style>body{font:14px system-ui;margin:20px;overflow-wrap:anywhere}table{border-collapse:collapse}th,td{border:1px solid #ddd;padding:8px;min-width:90px;max-width:360px;overflow-wrap:anywhere}th{background:#eee;position:sticky;top:0}td:nth-child(5){min-width:300px}.scroll{overflow:auto;max-height:78vh}input{padding:8px;max-width:100%;box-sizing:border-box}</style>'
        '<h1>故事与条件估值</h1><p>'+escape(note)+'</p><p>运行：'+escape(verified['run_id'])+'</p>'
        '<p><a href="'+detail_link+'">逐条参数、现金流、证据、反例与修订触发</a> · <a href="results.xlsx">Excel</a> · <a href="results.csv">CSV</a></p>'
        '<label>搜索代码、公司、故事或状态 <input id="search" type="search"></label><p id="count"></p><div class="scroll"><table>'+table+'</table></div>'
        '<script>const rows=[...document.querySelectorAll("table tr")].slice(1), count=document.querySelector("#count");count.textContent=rows.length+"行";document.querySelector("#search").addEventListener("input",e=>{let n=0;for(const r of rows){r.hidden=!r.textContent.toLowerCase().includes(e.target.value.toLowerCase());if(!r.hidden)n++;}count.textContent=n+"行"})</script></html>')
    lines = ['# 故事与条件估值', '', note, '', '| 代码 | 公司 | 经济画像 | 收盘价 | A条件值 | A价差% | B条件值 | B价差% |', '|---|---|---|---:|---:|---:|---:|---:|']
    for row in rows:
        items = row['stories']+[{}, {}]
        values = [row['code'], row['name'], row['profile'], row['price'],
            *[items[i].get(k) for i in range(2) for k in ('value_per_share', 'difference_pct')]]
        lines.append('| '+' | '.join(cell(v).replace('|', '／').replace('\n', ' ') for v in values)+' |')
    (output/'results.md').write_text('\n'.join(lines)+'\n')
    save(output/'manifest.json', dict(run_id=verified['run_id'], source_run_manifest_sha256=digest(run_dir/'manifest.json'),
        source_sqlite_sha256=document.snapshot['sqlite_sha256'], artifacts={p.name:digest(p) for p in sorted(output.iterdir())}))
    return {k:v for k,v in summary.items() if k != 'results'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepared', type=Path)
    mode.add_argument('--run', type=Path)
    parser.add_argument('--assignments', type=Path)
    parser.add_argument('--database', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.prepared:
        if args.assignments is None or args.database is not None:
            parser.error('--prepared requires --assignments; database is only for --run')
        print(build(args.prepared, args.assignments, args.output))
    else:
        if args.database is None or args.assignments is not None:
            parser.error('--run requires --database; assignments are read from verified run')
        print(report(args.run, args.database, args.output))


if __name__ == '__main__':
    main()
