"""把已核验的公司经营资料、标准事实与现有计算编排成初次覆盖；不生成预测参数。"""
from datetime import date
from collections import Counter
import csv
from decimal import Decimal
import html
import hashlib
import json
import math
import os
import shutil
from pathlib import Path
import re
import sqlite3

from data_sources.paths import workspace_path
from tools.initial_coverage import business_data, digest, report_folder, save, verify_inputs
from tools.research_charts import CONTRACT as CHART_CONTRACT, render


def money(value, divisor=100):
    return '未取得' if value is None else f'{float(value)/divisor:,.2f}'


def percent(value):
    return '未取得' if value is None else f'{float(value)*100:,.2f}%'


def clean(value):
    return re.sub(r'\s+', ' ', html.unescape(re.sub('<[^>]+>', '', str(value or '')))).strip().replace('|','／')


def table(headers, rows):
    return '\n'.join(['| '+' | '.join(headers)+' |', '|'+ '|'.join('---' for _ in headers)+'|',
                      *['| '+' | '.join(str(c).replace('|','／') for c in row)+' |' for row in rows]])


def disclosed_points(business):
    """按经营问题定位最新披露，引用只是线索，不是原文语义审核。"""
    if not business['reviews']:
        return []
    latest = max(r['period'] for r in business['reviews'])
    candidates = []
    themes = [('增长与交付', '收入|销量|产量|订单|客户|交付|用户|门店'),
              ('利润与竞争', '利润|成本|售价|降价|价格|毛利|竞争|费用|良率'),
              ('投入与回款', '现金|库存|存货|回款|应收|产能|研发|资本开支|融资')]
    for review in business['reviews']:
        if review['period'] != latest:
            continue
        for sentence in re.split('[。；;\n]', clean(review['text'])):
            if len(sentence)<18 or len(sentence)>220:
                continue
            matches = {name for name, pattern in themes if re.search(pattern, sentence)}
            theme = next((name for name in ('利润与竞争', '投入与回款', '增长与交付') if name in matches), None)
            if theme is None:
                continue
            role = '公司计划，尚未兑现' if re.search('预计|目标|计划|力争|有望|将实现|拟',sentence) else '公司经营披露，待原文核验'
            if sentence not in [p['text'] for p in candidates]:
                candidates.append(dict(period=latest,role=role,theme=theme,text=sentence,locator=review['locator']))
    points = []
    for name, _ in themes:
        matching = [p for p in candidates if p['theme'] == name]
        adverse = next((p for p in matching if re.search('下降|下滑|不及|减少|压力|延迟|风险|减值|亏损|收窄', p['text'])), None)
        for point in ([adverse] if adverse else []) + matching:
            if point not in points:
                points.append(point)
            if sum(p['theme'] == name for p in points) == 2:
                break
    return points


def select_segments(business, dimension):
    available = [r for r in business['segments'] if r['dimension']==dimension and r.get('name')]
    if not available:
        return []
    latest = max(r['period'] for r in available)
    return sorted([r for r in available if r['period']==latest],
                  key=lambda r: (r['revenue_cny'] is None, -(r['revenue_cny'] or 0), r['name']))


def source_value(facts, period, field):
    value = facts.get(period,{}).get('values_cny',{}).get(field)
    return None if value is None else float(Decimal(value))


def ratio(a, b):
    return None if a is None or b is None or b<=0 else a/b


def price_comparison(row, protocol, value, price):
    context = protocol.get('company_context', {}).get(row['ticker'], {})
    status = context.get('price_comparison_status', 'capital_date_proxy')
    if status not in ('capital_date_proxy', 'unreconciled_post_period_capital_change'):
        raise ValueError('未知报价比较政策：'+str(status))
    difference = value/price-1 if (status == 'capital_date_proxy' and value is not None
        and price is not None and price > 0) else None
    reason = (clean(context.get('reason')) if status != 'capital_date_proxy' else
        '资本基期代理的描述比较；没有完成全部期后融资及股类审查，不是严格可比的上涨空间。')
    return difference, status, reason


def guidance_rows(observations):
    rows=[]
    for observation in observations:
        metrics=[]
        for key,label in [('forecast_parent_net_income','归母净利'),('forecast_adjusted_net_income','扣非净利'),
                          ('forecast_revenue','收入'),('forecast_eps','EPS')]:
            low,high=observation.get(key+'_lower'),observation.get(key+'_upper')
            if low is not None or high is not None:
                metrics.append(label+' '+money(low,1)+'～'+money(high,1)+' '+str(observation.get(key+'_unit') or '单位未取得')
                    +'（指标状态：'+str(observation.get(key+'_status') or '未单列')+'）')
        description='；'.join(metrics) if metrics else clean(observation.get('title'))+'（本来源未给结构化金额）'
        reasons = [clean(r) for r in observation.get('reasons', []) if clean(r)]
        if reasons:
            description += '；公司解释（非独立验证）：'+ '；'.join(reasons)[:700]
        basis = {'ytd': '年初累计', 'quarter': '单季度', 'single_quarter': '单季度'}.get(observation.get('period_basis'), '期间口径待核')
        period = str(observation.get('period_start') or '起点未给出')+'至'+observation['period_end']+'／'+basis
        state = (observation['semantic_status']+'／'+str(observation.get('lifecycle') or '生命周期未给出')
            +'／来源'+('过期' if observation.get('source_stale') else '未标过期')
            +'／关联'+str(observation.get('verification') or '未给出'))
        rows.append([observation['source']+'／'+period, description,
            state+'／公告日'+str(observation['announcement_date'] or '未给出')+'／首次取得'+str(observation['first_observed_at'] or '未给出')])
    return rows


def company_observations(facts, model, report_end):
    """把已知同窗观察与实际模型条件并列，不从观察自动选择预测参数。"""
    end = date.fromisoformat(report_end)
    current, prior = end.isoformat(), end.replace(year=end.year-1).isoformat()
    f = lambda p, k: source_value(facts, p, k)
    revenue, old_revenue = f(current, 'revenue_cumulative'), f(prior, 'revenue_cumulative')
    growth = ratio(revenue, old_revenue)
    current_margin = ratio(f(current, 'reported_ebit'), revenue)
    old_margin = ratio(f(prior, 'reported_ebit'), old_revenue)
    revenue_observation = (f'{prior}与{current}同窗收入{money(old_revenue,1e8)}→{money(revenue,1e8)}亿元；'
        +'同比'+percent(None if growth is None else growth-1)+'。')
    margin_observation = f'同窗报表EBIT率{percent(old_margin)}→{percent(current_margin)}；分部毛利率与调整EBIT率另看。'
    balances = '；'.join(label+' '+money(f(prior, field),1e8)+'→'+money(f(current, field),1e8)+'亿元'
        for field, label in [('inventories','存货'),('accounts_receivable','应收'),('accounts_payable','应付')])
    cfo, net = f(current, 'operating_cash_flow_cumulative'), f(current, 'net_income_parent_ytd')
    cash_observation = f'同窗期末{balances}；当期合并经营现金{money(cfo,1e8)}亿元、归母净利{money(net,1e8)}亿元。'
    forecast = model['review']['forecast'] if model.get('review') else []
    if forecast:
        growth_condition = (f"下一滚动年收入{money(forecast[0]['revenue_million_cny'])}亿元、增长{percent(forecast[0]['growth'])}；"
            f"第{forecast[-1]['year']}年收入{money(forecast[-1]['revenue_million_cny'])}亿元。")
        margin_condition = (f"首年调整EBIT率{percent(forecast[0]['adjusted_operating_margin'])}、"
            f"末年{percent(forecast[-1]['adjusted_operating_margin'])}；需要业务组合、价格及费用共同支持。")
        release = model['review']['economic_checks']['capital_release']
        capital_condition = (f"模型全期投入{money(release['gross_investment_million_cny'])}、"
            f"释放{money(release['gross_release_million_cny'])}、净投入{money(release['net_reinvestment_million_cny'])}亿元；"
            '行业倍率及未来研发条件不因财务齐全而获验证。')
    else:
        growth_condition = margin_condition = capital_condition = '本次未计算DCF，不补造未来路径；已有财务仅用于经营研究。'
    return [
        ['增长', revenue_observation, growth_condition, '销量／客户或价格不能解释所需规模；小业务高增长无法抵消主业务放缓。'],
        ['利润率', margin_observation, margin_condition, '同口径利润率随降价、费用或客户结构恶化，改善依赖一次性事项而非持续经营。'],
        ['资本与现金', cash_observation, capital_condition, '同窗库存／应收上升未带来后续交付和回款，或已承诺投入不能容纳模型现金条件。'],
    ]


def cash_display(facts, base_year, end):
    annual=[f'{y}-12-31' for y in range(base_year-4,base_year+1)]
    pairs=sum(all(source_value(facts,p,k) is not None for k in ('net_income_parent_ytd','operating_cash_flow_cumulative')) for p in annual)
    periods=annual if pairs>=2 else [end.replace(year=end.year-1).isoformat(),end.isoformat()]
    series=[]
    for field,label in [('net_income_parent_ytd','归母净利润'),('operating_cash_flow_cumulative','经营现金流')]:
        values=[source_value(facts,p,field) for p in periods]
        if any(v is not None for v in values):
            series.append(dict(label=label,role='reported',values=[v/1e8 if v is not None else None for v in values]))
    return periods,series,'line' if pairs>=2 else 'bar'


def build_charts(folder, row, model, facts, business, collection):
    dcf, review = model['response']['dcf'], model['review']
    end = date.fromisoformat(business['report_end'])
    base = end.year-1 if end.month!=12 else end.year
    sources = [dict(id='valuation',label='冻结请求的实际API结果与现金流审阅',path=(folder/'valuation.json').relative_to(workspace_path()).as_posix(),
                    sha256=digest(folder/'valuation.json'),locator='$.response.dcf；$.review.forecast；$.sensitivities'),
               dict(id='facts',label='本次标准财务研究事实',path=(folder/'facts.json').relative_to(workspace_path()).as_posix(),
                    sha256=digest(folder/'facts.json'),locator='$.source_periods；元；年度及中报分开')]
    if collection is not None:
        sources.append(dict(id='business',label='东财转载的公司经营披露',path=collection['path'],sha256=collection['sha256'],
                            locator='$.zygcfx；期间、维度及行序见business.json；不是TDX总量'))
    charts = []
    segments = select_segments(business,'product') or select_segments(business,'industry')
    segments = [r for r in segments if r['name'] not in ('合计','总计','营业收入','营业总收入')]
    if any(r['revenue_cny'] is not None for r in segments):
        segments = segments[:6]
        charts.append(dict(id='business',kind='bar',orientation='horizontal',title='收入来自哪些已披露业务',unit='亿元人民币',
            period=segments[0]['period']+'；列示前6项以内，不与地区相加，不保证构成全部公司收入',
            labels=[clean(r['name'])[:14]+('…' if len(clean(r['name']))>14 else '') for r in segments],
            series=[dict(label='来源列示业务收入',role='reported',values=[None if r['revenue_cny'] is None else r['revenue_cny']/1e8 for r in segments])],
            source_ids=['business'],takeaway='业务构成解释收入对象；分部毛利和公司经营利润不是同一口径，不能把某项产品的高增长直接当作全公司增长。'))
    else:
        years=list(range(base-4,base+1))
        charts.append(dict(id='business',kind='bar',title='已有收入历史与经营规模',unit='亿元人民币',period='最近五个完整财年；缺项保留',
            labels=[str(y) for y in years],series=[dict(label='营业收入',role='reported',values=[
                None if source_value(facts,f'{y}-12-31','revenue_cumulative') is None else source_value(facts,f'{y}-12-31','revenue_cumulative')/1e8 for y in years])],
            source_ids=['facts'],takeaway='历史是解释经营的起点；未取得分部收入，不用行业份额或估计补造业务结构。'))
    cash_periods,cash_series,cash_kind=cash_display(facts,base,end)
    if cash_series:
        charts.append(dict(id='cash-quality',kind=cash_kind,title='利润有没有转化为经营现金',unit='亿元人民币',
            period='同窗完整年度' if cash_kind=='line' else '同窗中期；年度标准窗口不足两组，不用孤立点伪装五年趋势；经营现金流不是FCFF',
            labels=cash_periods,series=cash_series,source_ids=['facts'],takeaway='合并经营现金与归母净利归属范围不同，图示仅供线索对照；背离须检查周转、税费及少数股东等，不能用差额认定营运资本变化。'))
    forecast=review['forecast']
    charts.append(dict(id='forecast',kind='line',title='利润、再投资与可分配现金',unit='亿元人民币',
        period='冻结条件；财务基期'+business['report_end']+'后滚动年度，不是自然年预测',
        labels=['第'+str(r['year'])+'年' for r in forecast],source_ids=['valuation'],
        series=[dict(label=label,role='forecast',values=[r[field]/100 for r in forecast]) for field,label in
                [('nopat_million_cny','NOPAT'),('reinvestment_million_cny','总净再投资'),('fcff_million_cny','FCFF')]],
        takeaway='净再投资只扣一次；负值表示模型隐含释放，能否收回现金仍须资产与周转依据，不能当成已实现回收。'))
    bridge=dcf.get('equity_bridge_components',{})
    if dcf.get('value_of_equity') is not None and all(bridge.get(k) is not None for k in ('cash','cross_holdings','debt','minority','preferred')):
        charts.append(dict(id='equity',kind='waterfall',title='经营价值怎样变成普通股权益',unit='亿元人民币',
            period='原模型资本基期；总值与调整明细独立刻度；员工权利前权益',
            labels=['经营现值','现金代理','投资代理','债务','少数权益','优先索偿','权益值'],
            values=[dcf['value_of_operating_assets']/100,*[bridge[k]/100 for k in ('cash','cross_holdings','debt','minority','preferred')],dcf['value_of_equity']/100],
            types=['total',*['change']*5,'total'],source_ids=['valuation'],
            takeaway='现金和投资是既有口径代理；加回资产须与经营收益范围配套，索偿、期后融资及多股类限制仍保留。'))
    successful=[s for s in model['sensitivities'] if s['status']=='calculated']
    if successful:
        charts.append(dict(id='sensitivity',kind='dot',title='折现风险变化会怎样影响条件值',unit='人民币元/股',
            period='只改既有WACC水平入口±100基点；终值融资输入未另改，实际WACC另表列示；不是概率区间',
            labels=['入口下调1个百分点' if s['shift_bps']<0 else '入口上调1个百分点' for s in successful],
            reference=dict(value=model['response']['final']['value_per_share'],label='主条件 '+f"{model['response']['final']['value_per_share']:,.2f}"),
            series=[dict(label='实际计算条件值',role='forecast',values=[s['response']['final']['value_per_share'] for s in successful])],source_ids=['valuation'],
            takeaway='每个点均来自实际请求；终值输入、税盾与显式融资路径可能限制入口作用，不能把标签理解为全路径等幅变化。'))
    payload=dict(contract=CHART_CONTRACT,company=row['name'],sources=sources,charts=charts)
    return render(payload,folder/'charts')


def company_report(row, protocol, model, facts, business, profile, mechanism, peers, guidance, filings, *, guidance_queried=False):
    body,review=model['response'],model['review'];ltm=body['ltm_financials'];dcf=body['dcf'];forecast=review['forecast']
    end=date.fromisoformat(protocol['financial_report_period']);base=end.year-1 if end.month!=12 else end.year
    latest=end.isoformat();prior=end.replace(year=end.year-1).isoformat();fy=f'{base}-12-31'
    f=lambda p,k:source_value(facts,p,k)
    value=body['final']['value_per_share'];price=ltm.get('stock_price_reporting')
    change, comparison_status, comparison_reason = price_comparison(row, protocol, value, price)
    mode='当前SQLite绑定的原生来源初值与默认路径条件'
    paragraphs=[f"# {row['name']}（{row['ticker']}）初次覆盖",'',
        f"**每股条件价值 {value:,.2f} 元人民币**。本报告采用{mode}，给出该值需要兑现的经营表现及当前反证。"
        f"所用{row['quote_date'] or '未取得合格报价日'}未复权收盘价{money(price,1)}元，"
        +(f"描述价差{percent(change)}" if change is not None else '价差不比较')+'；不是预期收益率或新价格目标。','',
        f"**价格比较边界：** {comparison_reason}（`{comparison_status}`）。",'',
        f"本次为结构化公司研究与既有条件解释，计算请求事先冻结、经本地原生API重放，未按股价选故事或自动改增长、利润率、资本效率。财务／股本基期为{latest}；"
        f"估值输入截止{protocol['information_as_of']}，本次额外研究资料取得另行留痕。现金、投资与A股市值代理及期后资本变化限制继续保留。",'',
        '## 一、这是什么生意', '',
        f"{clean(profile['profile'].get('ORG_NAME'))}的披露主营为：**{clean(profile['profile'].get('MAIN_BUSINESS'))}**。",'',
        f"**商业机制解读：** {mechanism['meaning']}",'',
        f"本轮保留完整已发布沪深证券范围。参考分类为`{row['industry'] or '未关联'}`，东财经营分类为`{clean(profile['profile'].get('BOARD_NAME_LEVEL')) or '未取得'}`；两者用途不同，未据此改写主库分类或自动认证公司周期。",'']
    if row.get('previous_id'):
        paragraphs += [f"[前版研究](/research/{row['previous_id']})及旧公司专项条件保留；本轮统一当前原生条件，旧人工故事没有混入新请求。"
            '条件不同，不是同一组条件的价格更新或单因素改善，也不以新原生值覆盖前版专项结论。','']
    if value <= 0:
        paragraphs += ['**结论力度：** 本组原生条件给出非正普通股权益，保留其诊断含义；不能解释成股票将以负价格交易，也不是已完成困境或期权估值。','']
    if any(r['adjusted_operating_margin'] is not None and abs(r['adjusted_operating_margin']) > 1 for r in forecast):
        paragraphs += ['**利润范围需重点复核：** 实际路径含绝对值超过100%的调整经营利润率，不能只据计算成功认证合理。先核对收入基数、利润／资产范围、研发及周期；本轮没有为美化数值换参数。','']
    products=select_segments(business,'product') or select_segments(business,'industry');regions=select_segments(business,'region')
    if products:
        paragraphs += [f"### 业务构成：{products[0]['period']}",'',table(['来源列示业务','收入／亿元','来源占比','分部毛利率'],[
            [clean(r['name']),money(r['revenue_cny'],1e8),percent(r.get('revenue_share')),percent(r.get('gross_margin'))] for r in products[:10]]),'',
            '以上是同一业务维度的来源列示项，最多列前10项；产品、行业、地区不是可相加的不同收入。分部毛利率未扣总部销售／管理／研发等费用，不能代替估值中的经济EBIT率。','']
        leading=products[0]
        paragraphs += [f"**收入集中在哪里：** 当前列示金额最大的业务是“{clean(leading['name'])}”，收入{money(leading['revenue_cny'],1e8)}亿元、来源占比{percent(leading.get('revenue_share'))}。"
            '全公司增长需要这项核心业务的销量、实现价格或组合给出解释；较小新业务的高增速不能直接代表整体增长。没有客户／量价拆分时，增长贡献仍是待核对项。','']
    else:
        paragraphs += ['未取得可用业务构成，不把行业均值或公司经营范围中的许可事项补成实际收入。已有总收入、现金与条件估值仍可阅读。','']
    paragraphs += ['![公司业务与经营规模](charts/business.png)','',
        '图示帮助确认公司靠什么取得收入；金额沿披露期间，不拿半年结构充当TTM结构。','']
    if regions:
        paragraphs += [f"**地区暴露（{regions[0]['period']}）：** "+'；'.join(f"{clean(r['name'])} {money(r['revenue_cny'],1e8)}亿元" for r in regions[:5])+'。',
            '海外销售、当地生产与报表币种是不同概念；既有人民币WACC并未因此自动变成按各地区收入加权的公司风险估计。','']
    paragraphs += ['## 二、竞争与优势，需要什么证据','',
        '先看客户为何付款、产品或服务为何难被替代，再看维持这些条件需要付出什么。研发金额、公司知名度和单期毛利高都不能独自证明定价权。'
        f"对本公司，当前最直接的经营检验是：{mechanism['observe']}。",'']
    if peers:
        paragraphs += [table(['可比候选／不是竞争结论','披露主营简述','TTM收入／亿元','报表EBIT率'],[
            [p['name']+'（'+p['ticker']+'）',clean(p['business'])[:72],money(p['revenues']),percent(ratio(p['ebit'],p['revenues']))] for p in peers]),'',
            '同业候选先按国内经营分类匹配；不足时沿现有参考分类，按收入规模接近度选取，未按股价或估值筛选。它们的产品、租赁、自制／外包和地区范围可能不同，表中报表EBIT率不能直接作为本公司目标。','']
    points=disclosed_points(business)
    paragraphs += ['### 最近经营披露带来的线索','']
    latest_review=max((r['period'] for r in business['reviews']),default=None)
    if latest_review is not None and latest_review>latest:
        paragraphs += [f"**较新信息已纳入：** 经营资料已有{latest_review}披露，已进入本报告的观察与反证。财务基期仍为{latest}；"
            '较新经营披露不会被旧财务基期裁掉，也不直接用预告净利替换EBIT或拼接新的TTM。本轮没有自动修改原冻结请求，若新数据反证原条件，应显式重建财务与预测后再估值。','']
    if points:
        paragraphs += [table(['期间／主题／角色','经营披露摘录','定位'],[[p['period']+'／'+p['theme']+'／'+p['role'],clean(p['text']),p['locator']] for p in points]),'',
            '这些条目来自公司经营评述的二级汇编，财务总量仍取下表TDX标准值；营业总收入与营业收入、净利润与经营EBIT须分开。计划不当作已实现业绩，文本检索和引用不等于逐页原文审核。','']
    else:
        paragraphs += ['本次没有定位到适合短摘录的量化经营评述。业务结构与标准财务仍保留，缺项不以公司宣传或推测填补。','']
    paragraphs += ['## 三、财务体检：规模、利润与现金','',
        table(['完整年度','收入','报表EBIT','归母净利','经营现金流','研发费用','现金资本开支'],[
            [str(y),*[money(f(f'{y}-12-31',field),1e8) for field in ('revenue_cumulative','reported_ebit','net_income_parent_ytd','operating_cash_flow_cumulative','research_and_development_expense','capital_expenditure_cash')]] for y in range(base-4,base+1)]),'',
        '金额亿元人民币；未取得指本次标准查询未给出合格值，不代表TDX原包没有数据，亦可能受现行字段映射有效期限制；已列示0仍为0。历史窗口是研究展示，不是统一估值门槛。EBIT为报表经营利润，研发经济调整和FCFF另列。','',
        table(['中期／同窗比较','营业收入','报表EBIT','归母净利润','经营现金流'],[
            [p,*[money(f(p,field),1e8) for field in ('revenue_cumulative','reported_ebit','net_income_parent_ytd','operating_cash_flow_cumulative')]] for p in (prior,latest)]),'']
    now,then=f(latest,'revenue_cumulative'),f(prior,'revenue_cumulative')
    g=ratio(now,then);g=None if g is None else g-1
    paragraphs += [f"**增长的质量：** 最新同窗收入由{money(then,1e8)}亿元到{money(now,1e8)}亿元，可计算同比{percent(g)}。"
        f"本次实际模型TTM收入为{money(ltm['revenues'])}亿元；中期同比、TTM同比和未来第1年增长{percent(forecast[0]['growth'])}是不同窗口，不能互换。"
        '要解释增长质量，需要把上述主营与产品收入拆成数量、价格和组合，并检查是否由回款与现金支撑；本轮没有用半年乘二替代全年。','']
    bridge=review['margin_bridge']
    paragraphs += [f"**估值利润的口径：** 模型TTM报表EBIT {money(bridge['model_input_ebit'])}亿元，调整后经济EBIT {money(bridge['adjusted_ebit'])}亿元；"
        f"所用研发费用{money(bridge['research_expense'])}亿元、研发摊销{money(bridge['research_amortization'])}亿元、租赁调整{money(bridge['lease_adjustment'])}亿元。"
        '这是原引擎已消费的调整桥，不是把分部毛利或预告净利润改名为EBIT。','']
    net,cfo=f(fy,'net_income_parent_ytd'),f(fy,'operating_cash_flow_cumulative')
    paragraphs += [f"**利润与现金的线索：** {base}年归母净利{money(net,1e8)}亿元，合并经营现金流{money(cfo,1e8)}亿元。"
        +('已取得金额显示经营现金低于归母利润，库存、应收、税及一次性收付款是应核对的解释；仅据两者差额不能把现金缺口全归给营运资本。' if net is not None and cfo is not None and cfo<net
          else '经营现金相对利润的表现需要结合预收、应付、税及一次性收付款解释；一年的现金好转不足以证明长期自由现金。')
        +'两者归属范围不同，不计算严格现金转换率；经营现金流没有扣完经营增长所需总再投资，不能直接当FCFF。','']
    if cash_display(facts,base,end)[1]:
        paragraphs += ['![现金与利润](charts/cash-quality.png)','', '图中利润与经营现金使用相同期间；完整年度与中期不混画，空缺保留。','']
    paragraphs += ['### 资产负债与资本配置','',table(['期末存量／亿元',prior,fy,latest],[
        [label,money(f(prior,key),1e8),money(f(fy,key),1e8),money(f(latest,key),1e8)] for key,label in [('inventories','存货'),('accounts_receivable','应收账款'),('accounts_payable','应付账款'),('monetary_funds','货币资金'),('total_assets','总资产'),('total_liabilities','总负债')]]),'',
        '同比流量应配合同期余额看；年末到半年存量变化另列，不能与半年同比收入直接比较快慢。单项余额变化不等于已核实的非现金营运资本或现金回收。','',
        f"**投入与融资：** 最近完整年度现金资本开支{money(f(fy,'capital_expenditure_cash'),1e8)}亿元、研发费用{money(f(fy,'research_and_development_expense'),1e8)}亿元；"
        f"模型基期债务代理{money(ltm.get('bv_debt'))}亿元、现金代理{money(ltm.get('cash_and_marketable_securities'))}亿元。"
        '研发费用不等于全部当期研发现金，货币资金不等于可自由分配现金，经营负债也不等于全是有息债。投入支撑什么产品、多久收回、融资期限是否匹配，是资本配置的核心检验。','',
        f"**对管理层的具体问题：** {mechanism['observe']}。本报告不根据履历或公司自述给予管理层溢价；当前材料没有完成控制权、关联交易、员工权利及全部到期债务审查。"
        '已有研发、扩产与现金占用是行动证据，未来回报仍需客户与现金兑现。','',
        '## 四、经营故事与三个可证伪主张','',
        table(['主张主题','本公司已知观察','当前原生条件要求','反证／修订触发'],company_observations(facts,model,latest)),'',
        f"**关键分歧：** {clean(profile['profile'].get('MAIN_BUSINESS')) or row['name']}所披露的需求与投入，能否转化为上表所需收入、利润及现金？"
        f"主要替代解释是：{mechanism['counterevidence']}。当前材料尚不能唯一选定未来参数，先看{mechanism['observe']}，再决定是否提出新的显式研究条件。",'',
        '这些是依据本公司金额与实际条件建立的检验，不是发生概率或已被证据认证的新预测。缺少同窗值、量价或精确现金调节时保留未知。','']
    term=review['terminal'];release=review['economic_checks']['capital_release']
    capital_path=[r.get('capital_funding',{}).get('sales_to_capital') for r in forecast]
    capital_known=[v for v in capital_path if v is not None]
    capital_description=(f'{capital_known[0]:,.2f} → {capital_known[-1]:,.2f}倍（实际年度路径）' if capital_known
                         else '显式年度总净投入／实际预测表；不解读失效标量')
    research_selected=model['request']['inputs']['adjustment_inputs']['has_r_and_d']
    research_status=review['economic_checks']['research_forecast']['status']
    paragraphs += ['## 五、实际消费的假设与成立条件','',table(['参数／角色','实际值或路径端点','依据与待核对条件'],[
        ['未来收入增长',percent(forecast[0]['growth'])+' → '+percent(forecast[-1]['growth']),'沿冻结请求；近期历史不是未来预测，数量／价格／份额及持续期须相互匹配。'],
        ['调整经营利润率',percent(forecast[0]['adjusted_operating_margin'])+' → '+percent(forecast[-1]['adjusted_operating_margin']),'报表EBIT与研发／租赁调整分开；费用、竞争及规模兑现须支持。'],
        ['资本效率／净投入',capital_description,'年度路径优先；行业或存量倍率仅为当前条件，不自动是新增投资回报。'],
        ['初始人民币WACC',percent(body['cost_of_capital']['wacc']),'沿同一冻结请求的风险和融资输入；行业Beta、信用及权重代理仍需公司依据。'],
        ['实际无违约利率／权益风险溢价',percent(body['cost_of_capital'].get('risk_free_rate'))+'／'+percent(body['cost_of_capital'].get('equity_risk_premium')),'国债收益率与剥离违约风险后的无风险利率不同；总ERP不重复加国家风险。'],
        ['终值g／WACC／新增ROIC',percent(term['growth'])+'／'+percent(term['wacc'])+'／'+percent(term['assumed_marginal_roic']),'持续经营、竞争回报、税与再投资需一致；只满足WACC>g不能证明合理。'],
        ['终值再投资率',percent(term['reinvestment_rate']),'从实际结果读取，增长仍需投入；不是任意附加终值倍数。'],
        ['现金税口径',str(forecast[0]['operating_tax_basis']),'完整研发税务路径有则明列；没有不假设未来研发为0，也不自动加计扣除。']]),'',
        f"**本次研发分支：** {'已选历史研发资本化' if research_selected else '未选历史研发资本化'}；未来研发检查状态为`{research_status}`。"
        '有显式费用／税务路径时按该路径读取；没有时保留所选原生税务条件，未资本化也不等于公司不做研发。现金税模型代理不是完整税法审计。','',
        f"**资本释放需要特别说明：** 模型全期投入{money(release['gross_investment_million_cny'])}亿元、释放{money(release['gross_release_million_cny'])}亿元、净额{money(release['net_reinvestment_million_cny'])}亿元。"
        '三者不重复加扣。含释放时，需有可收回资产或周转现金的依据；没有释放时，也不能只因净投入为正就认为预算闭合。','',
        '## 六、从经营条件到现金与每股值','',
        table(['预测年','收入','调整利润率','经济EBIT','现金税','总净投入','FCFF'],[
            [str(r['year']),money(r['revenue_million_cny']),percent(r['adjusted_operating_margin']),money(r['economic_ebit_million_cny']),money(r['cash_tax_million_cny']),money(r['reinvestment_million_cny']),money(r['fcff_million_cny'])] for r in forecast]),'',
        '金额亿元；这些是实际引擎结果，不在报告重算FCFF。经济EBIT先经过现金税形成NOPAT，总净再投资只扣一次，研发净额已在所选处理中体现。','',
        '![利润、投入与FCFF](charts/forecast.png)','',
        f"**主要价值驱动：** 终值现值占经营价值{percent(term['value_share'])}；"
        f"预测第1年FCFF {money(forecast[0]['fcff_million_cny'])}亿元，末年{money(forecast[-1]['fcff_million_cny'])}亿元。"
        '增长本身不会自动创造价值，只有投入所换取的现金回报超过机会成本才创造价值。不能只看收入终点而忽略研发、周转、融资和长期竞争。','']
    bridge=dcf.get('equity_bridge_components',{})
    if dcf.get('value_of_equity') is not None and all(bridge.get(k) is not None for k in ('cash','cross_holdings','debt','minority','preferred')):
        paragraphs += [table(['权益桥／亿元','已计算金额'],[['经营现值',money(dcf['value_of_operating_assets'])],
            *[[label,money(bridge[key])] for key,label in [('cash','现金代理加回'),('cross_holdings','投资代理加回'),('debt','债务扣除'),('minority','少数权益扣除'),('preferred','优先索偿扣除')]],
            ['员工权利前权益',money(dcf['value_of_equity'])]]),'','![经营到股权](charts/equity.png)','']
    paragraphs += [f"**最终每股条件值：{value:,.2f}元**；实际股数{money(ltm.get('shares_outstanding'),100)}亿股，"
        f"员工权利扣除额{money(body['final'].get('value_of_all_options'))}亿元。数量未知时没有启用扣减，不代表已经证明没有稀释。"
        '可转债、其他股类及期后融资沿原请求处理，没有借图表重新估计。','']
    if value<=0:
        paragraphs += ['本组条件下普通股权益结果非正，报告保留原值。它揭示该经营、投入和融资组合的经济问题，不能解释为股票将以负价格交易，也没有把它静默截成0。','']
    paragraphs += ['## 七、实际对照、反证与下一次修订','']
    if not model['sensitivities']:
        paragraphs += ['本批没有额外运行参数敏感性；没有补造对照值、区间或敏感性图。下述是当前条件的经营反证，数值影响尚未另算。','']
    successful=[s for s in model['sensitivities'] if s['status']=='calculated']
    if successful:
        paragraphs += [table(['实际对照','初始WACC','终值WACC','终值ROIC','每股条件值／元'],[
            ['主条件',percent(body['cost_of_capital']['wacc']),percent(dcf['terminal_wacc']),percent(dcf['terminal_roic']),money(value,1)],
            *[['风险入口'+str(s['shift_bps'])+'bp',percent(s['response']['cost_of_capital']['wacc']),percent(s['response']['dcf']['terminal_wacc']),percent(s['response']['dcf']['terminal_roic']),money(s['response']['final']['value_per_share'],1)] for s in successful]]),'',
            '![风险敏感性](charts/sensitivity.png)','',
            '只修改现有WACC水平入口，其他请求值保留。实际终值与显式融资路径另列，未假定所有年份等幅变化，也未把这些点变成概率区间。','']
    rejected=[s for s in model['sensitivities'] if s['status']!='calculated']
    if rejected:
        paragraphs += ['未通过的敏感性也保留：'+'；'.join(str(s['shift_bps'])+'bp：'+clean(s['review'].get('reason'))[:160] for s in rejected)+'。拒绝不是估值0。','']
    paragraphs += [f"**最重要的观察：** {mechanism['observe']}。数据变化时，先核对收入对象、报表与调整利润、研发／现金税和真实投入，再显式修订条件；不能只因股价变化就选另一套故事。",'',
        '**风险传导：** '+mechanism['counterevidence']+'。这类反证既可能压低收入或利润，也可能增加存货／应收／资本开支，影响FCFF的幅度通常不等于利润变化本身。','']
    if guidance:
        paragraphs += ['### 已存近期业绩预告','',table(['来源／目标期间及累计／单季','指标／区间及公司解释（元或原单位）','状态／时间'],[
            *guidance_rows(guidance[:8])]),'',
            '预告是独立公司估计。年初至9月底累计不是7—9月单季；不同来源同一公告不算独立两份经营证据。公司解释用于检验需求、价格、成本及现金主张，生命周期、单指标状态与来源限制按原观测保留。未取区间中点、未写正式财报或TTM，净利润不能直接替换EBIT。本次并列观察，尚未量化进未来路径。','']
    elif guidance_queried:
        paragraphs += ['本次主库预告查询未为本公司存入观测；这不证明公司没有披露。原条件没有因未知预告被补0或自动调整。','']
    else:
        paragraphs += ['本轮未对本公司查询业绩预告，不能据此判断是否披露；没有将未知预告补0或写入原生预测。','']
    paragraphs += ['## 来源、时点与复核附件','',
        f"- [完整请求、本地原生API结果及经济审阅](valuation.json)；计算ID `{model['run_id']}`，本研究命名空间，不是网页会话ID。",
        '- [标准财务研究事实与按报告共享的血缘](facts.json)；与本次冻结消费快照按年度重叠金额核对，研究额外字段不回写估值输入。',
        '- [公司经营结构与评述定位](business.json)；东财二级汇编，不是新的财务主源。',
        '- [精确绘图数据](charts/chart-data.json)、[图注画廊](charts/gallery.md)、[图片版本与哈希](charts/manifest.json)。',
        f"- 公司概况："+(f"[原页面]({profile['source_page']})；披露原始发布日期未知，实际取得{profile['fetched_at']}。" if profile.get('source_page') else '未取得既有公司概况，不补造经营描述。'),
        '- 方法：[达摩达兰叙事与数字](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/NNPreface.html)；估值定义沿冻结引擎，不因批量覆盖改写。','']
    for filing in filings:
        paragraphs.append('- [官方'+filing['period']+'报告原文]('+filing['source_url']+')；目录身份已解析，本轮不冒称逐页重读全文。')
    paragraphs += ['', '原文金额核验、算术一致、经营依据与预测有效性分别看待。本轮真实API与现金流闭合通过，不能追认为未来预测准确。'
        '本文是可追溯的结构化初次覆盖；公司计划、二级经营资料、行业参考和本次判断的边界均保留。', '']
    return '\n'.join(paragraphs)



def partial_company_report(row, protocol, model, facts, business, profile, mechanism, guidance, *, guidance_queried=False):
    status, financial = row['admission'], row.get('financial_admission', '未给出')
    missing = '、'.join(str(v) for v in row.get('missing_fields', [])) or '未列必需财务缺项'
    reason = clean(model.get('reason')) or clean(row.get('reason')) or '按现行准入状态保留，未生成DCF。'
    period = protocol['financial_report_period']
    paragraphs = [f"# {row['name']}（{row['ticker']}）初次覆盖：范围与输入状态", '',
        f"**本次没有每股DCF条件值。** 产品准入`{status}`；财务输入状态`{financial}`。{reason}", '',
        '这是当前非金融DCF路径的状态，不表示该公司经济价值为0或不存在其他估值方法。金融专项、未审核范围及历史不足估计沿当前约定延期。', '',
        '## 公司与已知业务', '',
        clean(profile['profile'].get('MAIN_BUSINESS')) or '现有研究缓存未取得可核对主营，不按公司名称或行业标签猜业务。', '',
        '## 已有财务与缺项', '',
        f"财务研究窗口截至{period}；本次未计算估值，因此未设估值股本基期。消费快照截止{protocol['information_as_of']}；本轮范围／请求冻结{protocol['frozen_at']}。", '',
        f"已有最近合格报价：{money(row.get('stock_price_listing'),1)}元，交易日{row.get('quote_date') or '未取得'}。"
        '本次未计算DCF所以不比较价差；范围外或财务受阻不等于行情缺失。', '',
        f"必需输入缺项：{missing}。范围审核与财务完整性分别记录，财务已有值不因产品范围外被当作缺失或删除。", '',
        table(['研究主题', '已知观察', '后续条件', '应核对的反证'], company_observations(facts, model, period)), '',
        '来源金额元，正文换算亿元；同窗值缺失保留未取得，不补零或扩采早年。合并经营现金与归母净利润归属范围不同，不计算严格现金转换率。', '',
        '## 近期经营信息与用途', '',
    ]
    segments = select_segments(business, 'product') or select_segments(business, 'industry')
    if segments:
        paragraphs += [table(['业务／期间', '收入／亿元', '来源占比'], [
            [clean(r['name'])+'／'+r['period'], money(r['revenue_cny'], 1e8), percent(r.get('revenue_share'))] for r in segments[:10]]), '',
            '仅列同一业务维度；产品、地区与行业不可相加，半年结构不等于TTM。', '']
    points = disclosed_points(business)
    if points:
        paragraphs += [table(['期间／主题', '公司披露线索', '角色与定位'], [
            [p['period']+'／'+p['theme'], clean(p['text']), p['role']+'／'+p['locator']] for p in points]), '']
    if guidance:
        paragraphs += [table(['来源／期间及累计或单季', '区间及公司解释', '状态与时间'], guidance_rows(guidance[:8])), '',
            '预告未进入正式财报、TTM或未来参数；净利润不改名EBIT，不取区间中点。', '']
    elif guidance_queried:
        paragraphs += ['本次预告查询没有本公司已存观测，不等于公司未披露。', '']
    else:
        paragraphs += ['本轮未对本公司查询业绩预告，不以未知冒充未披露或无预告。', '']
    paragraphs += ['## 下一步与边界', '',
        '按缺项类别处理已有来源、关联或契约问题；历史不足估计、金融专项和新分类目前延后。本报告保留状态与已知资料，不为转绿生成未来研发、预测或行业映射。', '',
        '[本次准入与API诊断](valuation.json) · [标准研究事实](facts.json) · [经营资料及来源定位](business.json)', '']
    if row.get('previous_id'):
        paragraphs += [f"[前版研究](/research/{row['previous_id']})保留；未把旧人工条件带入本次快照。", '']
    return '\n'.join(paragraphs)


def protocol_resource(protocol, key):
    value = protocol[key]
    path = workspace_path(value)
    if Path(value).is_absolute() or not path.resolve().is_relative_to(workspace_path().resolve()) or not path.is_file():
        raise ValueError('协议来源必须是workspace内已存在的相对路径：'+key)
    return path


def read_guidance(protocol):
    if not protocol.get('guidance_file'):
        return dict(companies=[], observations=[])
    packet = json.loads(protocol_resource(protocol, 'guidance_file').read_text())
    scope = {row['ticker'] for row in protocol['companies']}
    if (packet.get('contract_version') != 'earnings-guidance-context-v1'
            or not isinstance(packet.get('companies'), list) or not isinstance(packet.get('observations'), list)
            or any(not isinstance(row, dict) or row.get('ticker') not in scope
                   for row in packet['companies']+packet['observations'])):
        raise ValueError('业绩预告查询契约或证券范围与本次冻结范围不一致')
    return packet


def verify_materials(protocol, protocol_hash, financial, collection):
    expected = {
        'source_database_sha256': protocol['source_database_sha256'],
        'sqlite_sha256': protocol['sqlite_sha256'],
        'report_end': protocol['financial_report_period'],
        'research_information_as_of': protocol['frozen_at'],
        'valuation_information_as_of': protocol['information_as_of'],
    }
    if (financial.get('contract') != 'initial-coverage-standard-facts-v2'
            or any(financial.get(key) != value for key, value in expected.items())
            or set(financial['company_offsets']) != {row['ticker'] for row in protocol['companies']}
            or financial['companies_count'] != len(protocol['companies'])
            or financial.get('differences') != []):
        raise ValueError('标准研究资料不是本次完整冻结范围／快照')
    if digest(protocol_resource(financial, 'company_facts_file')) != financial['company_facts_sha256']:
        raise ValueError('标准逐公司研究事实文件已变化')
    if (collection.get('protocol_sha256') != protocol_hash
            or not set(collection['records']).issubset(financial['company_offsets'])):
        raise ValueError('经营资料不是本次冻结研究协议')


def company_facts(stream, financial, ticker):
    offset, length = financial['company_offsets'][ticker]
    if type(offset) is not int or type(length) is not int or offset < 0 or not 0 < length <= 4 << 20:
        raise ValueError('逐公司研究事实定位不合格')
    stream.seek(offset)
    raw = stream.read(length)
    if len(raw) != length:
        raise ValueError('逐公司研究事实文件被截断')
    packet = json.loads(raw)
    if packet.get('ticker') != ticker or set(packet) != {'ticker', 'source_periods'}:
        raise ValueError('逐公司研究事实证券身份错配')
    return packet['source_periods']


def verify_model(model, row, protocol, protocol_hash):
    from engine.data_dictionary import CompanyValuationInput
    from tools.check_native_sqlite import value_digest
    if (model.get('contract') != 'initial-coverage-calculation-v2'
            or model.get('protocol_sha256') != protocol_hash or model.get('ticker') != row['ticker']
            or model.get('admission') != row['admission']
            or model.get('financial_input_snapshot') != protocol['sqlite_sha256']):
        raise ValueError('公司计算不是本次冻结身份／准入／快照：'+row['ticker'])
    if row['admission'] == 'ready':
        if (not model.get('api_verified')
                or model['response'].get('reference_snapshot', {}).get('id') != protocol['reference_snapshot_id']):
            raise ValueError('可计算公司尚未完成同版原生本地API核验：'+row['ticker'])
        response, request = model['response'], model['request']
        if (response.get('ticker') != row['ticker'] or response['inputs'].get('ticker') != row['ticker']
                or request['inputs'].get('ticker') != row['ticker']
                or value_digest(request['inputs']) != row['inputs_sha256']
                # API按类型规范化数字与UTC日期；原请求哈希仍严格绑定冻结快照。
                or CompanyValuationInput.model_validate(response['inputs']).model_dump(mode='json')
                    != CompanyValuationInput.model_validate(request['inputs']).model_dump(mode='json')):
            raise ValueError('公司请求或响应身份／实际输入与冻结快照不一致：'+row['ticker'])
    elif value_digest(model['diagnostic']) != row['diagnostic_sha256']:
        raise ValueError('公司准入诊断与冻结快照不一致：'+row['ticker'])


def build(protocol_file, tickers=None, revision=None):
    """沿完整冻结名单交付；未知、范围外和采集失败保留独立研究状态。"""
    from data_sources.research_reports import publish, read_report, verified_file
    work = protocol_file.parent
    protocol = json.loads(protocol_file.read_text())
    if protocol.get('contract') != 'initial-coverage-protocol-v2':
        raise ValueError('本入口只消费当前initial-coverage-protocol-v2')
    verify_inputs(protocol, runtime=True)
    protocol_hash = digest(protocol_file)
    if revision is not None and not re.fullmatch(r'v[2-9][0-9]*', revision):
        raise ValueError('修订须显式命名v2以上版本')
    financial = json.loads((work/'financial-facts.json').read_text())
    collection = json.loads((work/'business-collection.json').read_text())
    verify_materials(protocol, protocol_hash, financial, collection)
    profile_file = protocol_resource(protocol, 'profile_inventory')
    profiles = json.loads(profile_file.read_text())['records']
    questions_file = protocol_resource(protocol, 'questions_file')
    questions = json.loads(questions_file.read_text())
    questions_hash = digest(questions_file)
    inventory = json.loads(protocol_resource(protocol, 'filing_inventory').read_text())
    guidance = read_guidance(protocol)
    guidance_queried = {row['ticker'] for row in guidance['companies']}
    by_guidance, filings_by_code = {}, {}
    for observation in guidance['observations']:
        by_guidance.setdefault(observation['ticker'], []).append(observation)
    for filing in inventory:
        if (filing['filing_variant'] == 'full' and filing['resolution_status'] == 'resolved'
                and filing['period'] in (protocol['financial_report_period'], str(date.fromisoformat(protocol['financial_report_period']).year-1)+'-12-31')):
            filings_by_code.setdefault(filing['provider_code'], []).append(filing)
    with sqlite3.connect(workspace_path('valuation.sqlite').as_uri()+'?mode=ro', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        native = {r['ticker']: dict(r) for r in connection.execute('SELECT * FROM financials_ttm')}
    selected = {r['ticker']: r for r in protocol['companies']}
    if len(selected) != len(protocol['companies']):
        raise ValueError('冻结名单出现重复证券')
    requested = set(tickers or selected)
    if not requested.issubset(selected):
        raise ValueError('试运行证券在冻结范围之外')
    domestic_peers, reference_peers = {}, {}
    for ticker, row in selected.items():
        if row['admission'] != 'ready' or ticker not in profiles:
            continue
        domestic = profiles[ticker]['profile'].get('BOARD_NAME_LEVEL')
        if domestic:
            domestic_peers.setdefault(domestic, []).append(ticker)
        if row.get('industry'):
            reference_peers.setdefault(row['industry'], []).append(ticker)
    results = []
    with protocol_resource(financial, 'company_facts_file').open('rb') as facts_stream:
        for ticker, row in selected.items():
            if ticker not in requested:
                continue
            base_folder = report_folder(ticker, protocol)
            folder = base_folder.with_name(base_folder.name+'-'+revision) if revision else base_folder
            if (folder/'research.json').exists():
                existing = read_report(folder.name)
                model = json.loads((folder/'valuation.json').read_text())
                verify_model(model, row, protocol, protocol_hash)
                for entry in existing.files:
                    verified_file(existing, entry.id)
                graph = json.loads((folder/'charts/manifest.json').read_text()) if (folder/'charts/manifest.json').exists() else {'files': []}
                results.append(dict(ticker=ticker, id=folder.name, status='reused_frozen_report', admission=row['admission'],
                    charts=len(graph['files']), files=len(existing.files)))
                continue
            if (folder/'charts').exists():
                raise ValueError('未登记的旧图表草稿须先核对，不静默覆盖：'+ticker)
            if folder != base_folder:
                folder.mkdir(parents=True, exist_ok=True)
                if not (folder/'valuation.json').exists():
                    shutil.copyfile(base_folder/'valuation.json', folder/'valuation.json')
            model = json.loads((folder/'valuation.json').read_text())
            verify_model(model, row, protocol, protocol_hash)
            ready = row['admission'] == 'ready'
            source = collection['records'].get(ticker)
            business = dict(ticker=ticker, report_end=protocol['financial_report_period'], segments=[], reviews=[], business_scopes=[])
            if source is not None:
                raw = workspace_path(source['path'])
                if digest(raw) != source['sha256']:
                    raise ValueError('经营原始响应哈希改变')
                business = business_data(raw.read_bytes(), ticker, protocol['financial_report_period'], source['fetched_at'][:10])
            profile = profiles.get(ticker) or dict(profile={}, source_file=None, source_sha256=None, source_locator=None,
                fetched_at=None, source_page=None)
            if profile.get('source_file') and digest(workspace_path(profile['source_file'])) != profile['source_sha256']:
                raise ValueError('公司概况来源改变')
            rule = next((rule for rule in questions['rules'] if re.search(rule['pattern'], profile['profile'].get('MAIN_BUSINESS') or '')), None)
            if rule is None:
                fallback = questions['fallback']
                rule = dict(name=fallback[0], meaning=fallback[2], counterevidence=fallback[3], observe=fallback[4])
            domestic = profile['profile'].get('BOARD_NAME_LEVEL')
            candidates = domestic_peers.get(domestic, []) or reference_peers.get(row.get('industry'), [])
            scale = native.get(ticker, {}).get('revenues')
            def distance(peer):
                revenue = native[peer].get('revenues')
                return (abs(math.log(revenue/scale)) if revenue and scale and revenue > 0 and scale > 0 else math.inf, peer)
            peers = [dict(ticker=peer, name=selected[peer]['name'], business=profiles[peer]['profile'].get('MAIN_BUSINESS') or '',
                revenues=native[peer].get('revenues'), ebit=native[peer].get('ebit'))
                for peer in sorted((p for p in candidates if p != ticker), key=distance)[:3]]
            facts = company_facts(facts_stream, financial, ticker)
            save(folder/'facts.json', dict(contract='initial-coverage-company-facts-v2', ticker=ticker,
                source_database_sha256=financial['source_database_sha256'], sqlite_sha256=financial['sqlite_sha256'],
                research_information_as_of=financial['research_information_as_of'], valuation_information_as_of=financial['valuation_information_as_of'],
                source_periods=facts, source_role='current_standard_facts_for_research',
                native_annual_overlap_verified=True, peers=peers,
                body_native_ltm=model['response'].get('ltm_financials') if ready else None))
            save(folder/'business.json', dict(contract='initial-coverage-business-evidence-v2', ticker=ticker,
                source=source, segments=business['segments'],
                review_locations=[dict(period=r['period'], locator=r['locator'], characters=len(r['text'])) for r in business['reviews']],
                selected_quantitative_disclosures=disclosed_points(business),
                profile_source={k: profile.get(k) for k in ('source_file', 'source_sha256', 'source_locator', 'fetched_at', 'source_page')},
                mechanism=dict(**rule, role='research_questions_not_forecast_or_company_cycle_classification'),
                mechanism_source_sha256=questions_hash, guidance=by_guidance.get(ticker, []),
                guidance_query_status='queried' if ticker in guidance_queried else 'not_queried',
                official_filing_directory=filings_by_code.get(ticker[5:], []),
                company_context=protocol.get('company_context', {}).get(ticker)))
            manifest = build_charts(folder, row, model, facts, business, source) if ready else {'files': []}
            text = (company_report(row, protocol, model, facts, business, profile, rule, peers,
                        by_guidance.get(ticker, []), filings_by_code.get(ticker[5:], []), guidance_queried=ticker in guidance_queried) if ready
                    else partial_company_report(row, protocol, model, facts, business, profile, rule, by_guidance.get(ticker, []),
                        guidance_queried=ticker in guidance_queried))
            source_limitation = ready and (source is None or not profile.get('source_file'))
            if source_limitation:
                text += '\n经营来源存在局部缺项；已知标准财务与原生条件保留，本报告不冒称经营研究资料全部取得。\n'
            text += f"\n[本轮全部沪深公司覆盖总览](/research/{protocol['catalogue_id']})\n"
            (folder/'report.md').write_text(text)
            paths = [('report', '公司初次覆盖正文', folder/'report.md'), ('valuation', '本次请求、结果或准入诊断', folder/'valuation.json'),
                ('facts', '标准研究事实及源记录', folder/'facts.json'), ('business-evidence', '经营证据与来源定位', folder/'business.json')]
            if ready:
                paths += [('chart-data', '精确绘图数据', folder/'charts/chart-data.json'),
                    ('chart-manifest', '图表版本与哈希', folder/'charts/manifest.json'), ('gallery', '图注画廊', folder/'charts/gallery.md')]
            if profile.get('source_file'):
                paths.append(('profile-source', '公司概况原始响应', workspace_path(profile['source_file'])))
            if source:
                paths.append(('business-source', '公司经营披露原始响应', workspace_path(source['path'])))
            capital_evidence = protocol.get('company_context', {}).get(ticker, {}).get('evidence')
            if capital_evidence:
                paths.append(('capital-context', '价格比较及期后资本证据', workspace_path(capital_evidence['path'])))
            files = [dict(id=identifier, label=label, path=path.relative_to(workspace_path()).as_posix(), sha256=digest(path))
                for identifier, label, path in paths]
            files += [{k: f[k] for k in ('id', 'label', 'path', 'sha256')} for f in manifest['files']]
            value = model['response']['final']['value_per_share'] if ready else None
            summary = (profile['profile'].get('MAIN_BUSINESS') or '已有资料与范围／输入状态逐项保留。')[:180]
            summary += ('；每股原生条件值'+f'{value:,.2f}'+'元，尚非已验证公司预测。') if ready else '；未生成DCF：'+row['admission']
            metadata = dict(contract='alphalake-research-v1', id=folder.name,
                title=row['name']+'：业务、财务与'+('条件估值' if ready else '范围／缺项')+'｜初次覆盖', summary=summary,
                kind='initial_coverage', status='complete' if ready and not source_limitation else 'partial', tickers=[ticker],
                research_date=collection['acquired_at'][:10], financial_period=protocol['financial_report_period'],
                information_as_of=collection['acquired_at'], share_date=protocol['financial_report_period'] if ready else None,
                run_ids=[model['run_id']] if model.get('run_id') else [], files=files, limitations=[
                    '结构化初次覆盖；经营汇编与已读材料分别记录，未逐页独立审核整份年报／中报。',
                    '统一解释本次消费快照的原生条件；没有新增经济参数自动选择、概率或默认采用。',
                    '财务、消费快照截止、研究冻结及经营来源实际取得分别留痕，不认证历史PIT。',
                    '现金／投资／A股市值、未来研发税务、期后资本结构及员工权利未闭合限制保留。'])
            previous_id = base_folder.name if revision and (base_folder/'research.json').exists() else row.get('previous_id')
            if previous_id:
                metadata['previous_id'] = previous_id
            report = publish(folder, metadata)
            for entry in report.files:
                verified_file(report, entry.id)
            results.append(dict(ticker=ticker, id=folder.name, status='published', admission=row['admission'],
                charts=len(manifest['files']), files=len(report.files), value_per_share=value,
                mode=model.get('mode'), report_characters=len(text)))
            if len(results) % 25 == 0:
                print(json.dumps(dict(delivered=len(results), target=len(requested), latest=ticker)), flush=True)
    summary = dict(contract='initial-coverage-build-v2', requested=len(requested), delivered=len(results), results=results)
    current_file = work/'current-reports.json'
    current = json.loads(current_file.read_text()) if current_file.exists() else {}
    current.update({r['ticker']: r['id'] for r in results})
    save(current_file, current)
    scope_key = hashlib.sha256(','.join(sorted(requested)).encode()).hexdigest()[:12]
    save(work/(('revision-'+revision+'-build-'+scope_key+'.json') if revision else 'pilot-build.json' if tickers else 'build-summary.json'), summary)
    verify_inputs(protocol)
    if digest(protocol_resource(financial, 'company_facts_file')) != financial['company_facts_sha256']:
        raise ValueError('交付期间逐公司研究事实变化')
    return {k: v for k, v in summary.items() if k != 'results'}


def catalogue_pages(folder, results):
    page_files, pages = [], []
    for start in range(0, len(results), 250):
        page_number = start//250+1
        page = folder/f'companies-{page_number:02}.md'
        part = results[start:start+250]
        content = [f'# 全部公司覆盖：第{page_number}页', '', '[返回覆盖总览](/research/'+folder.name+')', '',
            table(['公司／独立报告', '产品准入', '财务输入状态', '每股条件值／元', '收盘价／日期', '描述价差'], [
                [f"[{r['name']}（{r['ticker']}）]({r['url']})", r['admission'], str(r['financial_admission']),
                 money(r['condition_value_cny_per_share'], 1), money(r['close_cny'], 1)+'／'+str(r['close_date'] or '未取得'),
                 '未比较' if r['descriptive_difference_pct'] is None else f"{r['descriptive_difference_pct']:.2f}%"] for r in part]), '',
            '条件值不是目标价或预期收益；未计算与缺项不填0，范围外不删分母。', '']
        page.write_text('\n'.join(content))
        pages.append([f'[第{page_number}页]({page.name})', part[0]['ticker']+'—'+part[-1]['ticker'], str(len(part))])
        page_files.append(('companies-'+str(page_number), '全公司列表第'+str(page_number)+'页', page))
    return page_files, pages


def catalogue(protocol_file, revision=None):
    from data_sources.research_reports import publish, read_report, verified_file
    from tools.check_native_sqlite import value_digest
    work = protocol_file.parent
    protocol = json.loads(protocol_file.read_text())
    if protocol.get('contract') != 'initial-coverage-protocol-v2':
        raise ValueError('本入口只消费当前initial-coverage-protocol-v2')
    verify_inputs(protocol, runtime=True)
    protocol_hash = digest(protocol_file)
    if revision is not None and not re.fullmatch(r'v[2-9][0-9]*', revision):
        raise ValueError('修订须显式命名v2以上版本')
    collection = json.loads((work/'business-collection.json').read_text())
    financial = json.loads((work/'financial-facts.json').read_text())
    verify_materials(protocol, protocol_hash, financial, collection)
    current_reports = json.loads((work/'current-reports.json').read_text())
    comparisons = json.loads((work/'financial-comparison-summary.json').read_text())
    if set(current_reports) != {r['ticker'] for r in protocol['companies']}:
        raise ValueError('报告交付范围不完整或混入额外证券，不发布完成总览')
    results, modes, chart_counts, risks, admissions, research_states = [], Counter(), Counter(), Counter(), Counter(), Counter()
    references = 0
    for row in protocol['companies']:
        ticker, identifier = row['ticker'], current_reports[row['ticker']]
        report = read_report(identifier)
        if report.tickers != [ticker] or report.kind != 'initial_coverage':
            raise ValueError('不是独立公司初次覆盖')
        for entry in report.files:
            verified_file(report, entry.id)
        folder = workspace_path('research', identifier)
        model = json.loads((folder/'valuation.json').read_text())
        verify_model(model, row, protocol, protocol_hash)
        ready = row['admission'] == 'ready'
        body = model['response'] if ready else None
        graph = json.loads((folder/'charts/manifest.json').read_text()) if ready else {'files': []}
        value = body['final']['value_per_share'] if ready else None
        price = body['ltm_financials']['stock_price_reporting'] if ready else row.get('stock_price_listing')
        modes[model.get('mode') or row['admission']] += 1
        chart_counts[len(graph['files'])] += 1
        admissions[row['admission']] += 1
        research_states[report.status] += 1
        references += len(report.files)
        if ready:
            checks = model['review']['economic_checks']
            if value <= 0:
                risks['nonpositive_equity_condition'] += 1
            if checks['capital_release']['gross_release_million_cny'] > 0:
                risks['capital_release_requires_recoverability_basis'] += 1
            if model['request']['inputs']['adjustment_inputs']['has_r_and_d'] and model['request']['inputs']['valuation_assumptions'].get('research_tax') is None:
                risks['capitalized_research_without_explicit_future_tax_path'] += 1
            if any(r['adjusted_operating_margin'] is not None and abs(r['adjusted_operating_margin']) > 1 for r in model['review']['forecast']):
                risks['forecast_margin_outside_plus_minus_100_percent'] += 1
            risks['sensitivity_calculated'] += sum(s['status'] == 'calculated' for s in model['sensitivities'])
            risks['sensitivity_rejected'] += sum(s['status'] != 'calculated' for s in model['sensitivities'])
        evidence = json.loads((folder/'business.json').read_text())
        if evidence['source'] is None and ready:
            risks['business_source_missing'] += 1
        if not evidence['profile_source']['source_file']:
            risks['profile_source_missing'] += 1
        difference, comparison_status, comparison_reason = price_comparison(row, protocol, value, price)
        results.append(dict(ticker=ticker, name=row['name'], instrument_id=row['instrument_id'],
            reference_industry=row.get('industry'), report_id=identifier, url='/research/'+identifier,
            admission=row['admission'], financial_admission=row.get('financial_admission'), missing_fields=row.get('missing_fields', []),
            reason=row.get('reason'), research_status=report.status,
            mode=model.get('mode'), condition_value_cny_per_share=value, close_cny=price, close_date=row.get('quote_date'),
            descriptive_difference_pct=difference*100 if difference is not None else None,
            price_comparison_status=comparison_status, price_comparison_reason=comparison_reason,
            financial_period=protocol['financial_report_period'], information_as_of=protocol['information_as_of'],
            research_frozen_at=protocol['frozen_at'], share_date=protocol['financial_report_period'] if ready else None, run_id=model.get('run_id'),
            registered_files=len(report.files), charts=len(graph['files']), main_report_sha256=digest(folder/'report.md'),
            complete_pdf_semantic_review=False, forecast_economic_certification=False))
    base_folder = workspace_path('research', protocol['catalogue_id'])
    folder = base_folder.with_name(base_folder.name+'-'+revision) if revision else base_folder
    folder.mkdir(parents=True, exist_ok=True)
    if (folder/'research.json').exists():
        raise ValueError('总览已登记，修订须新建ID')
    summary = dict(contract='initial-coverage-all-v1', count=len(results), source_universe=protocol['source_universe'],
        admissions=dict(admissions), research_statuses=dict(research_states), modes=dict(modes), risk_and_diagnostic_counts=dict(risks),
        chart_count=sum(r['charts'] for r in results), chart_distribution=dict(chart_counts), company_references=references,
        protocol_sha256=protocol_hash, sqlite_sha256=protocol['sqlite_sha256'], report_period=protocol['financial_report_period'],
        valuation_information_as_of=protocol['information_as_of'], research_frozen_at=protocol['frozen_at'],
        company_evidence_acquired_at=collection['acquired_at'], results=results, annual_input_comparison=comparisons,
        automatic_parameter_selection=False, default_parameters_changed=False, pdf_full_text_independent_reviews=0)
    summary['run_id'] = value_digest(summary)
    save(folder/'results.json', summary)
    with (folder/'results.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(results[0]))
        writer.writeheader()
        for result in results:
            writer.writerow(result | dict(missing_fields=json.dumps(result['missing_fields'], ensure_ascii=False)))
    page_files, pages = catalogue_pages(folder, results)
    narrative = [f"# 全部{len(results):,}家已发布沪深公司初次覆盖", '',
        f"**{len(results):,}家逐一交付研究页；{admissions['ready']:,}家有当前原生条件值，其余{len(results)-admissions['ready']:,}家保留范围或输入状态。** "
        '研究交付不等于全部公司已完成DCF，也不等于未来预测已获验证。', '',
        '## 本轮采用什么起点', '',
        '证券名单按当前发布消费快照的规范身份完整冻结，不按市值、估值高低或资料齐全程度删选。默认沪深范围，不含北交所。', '',
        f"财务／股本基期{protocol['financial_report_period']}；估值输入截止{protocol['information_as_of']}；范围／请求冻结{protocol['frozen_at']}；经营资料收集截至{collection['acquired_at']}。"
        '报价按每家公司实际收盘日列示，旧价与未知价格保留。经营资料的期间和实际取得分别留痕，不把新取得冒充当时已经留存。', '',
        '全部可计算公司统一沿当前SQLite绑定的原生请求，经本地原生API逐个重放；旧人工故事只作前版链接，没有混入新请求。条件不同不是同一组条件的价格更新或单因素改善。'
        '实际服务HTTP抽样验收与本地全范围API核验分别记录，本总览不声称逐家公司在8080重新POST。', '',
        '## 如何阅读公司研究', '',
        '- 每家公司独立展示已知业务、标准财务、同窗观察、当前条件及反证；增长、利润和资本的矛盾作为待检验解释，不按正则问句自动认证周期或预测。',
        '- 最新经营披露及已存预告列明实际期间、累计或单季、来源状态和用途；预告未写入财报／TTM，也未自动转为未来参数。',
        '- 经营汇编未取得的公司保留局部覆盖与标准财务，不称全文已读；没有已计算敏感性则不补造对照。金融范围外、范围待审及缺必需输入各有独立partial页。',
        '- 每股条件值、现金／投资与市值代理、未来研发税、资本释放、终值、期后股本及员工权利限制沿实际请求保留。已知期后资本未闭合时显示报价但不列价差；其他是资本基期代理的描述比较，未完成全员期后融资审查。计算成功与经济依据、预测有效性分别评价。', '',
        table(['产品准入状态', '公司数'], [[state, str(count)] for state, count in sorted(admissions.items())]), '',
        table(['研究交付状态', '公司数'], [[state, str(count)] for state, count in sorted(research_states.items())]), '',
        '## 重要审阅项（类别可重叠）', '',
        table(['诊断', '公司数'], [[key, str(count)] for key, count in sorted(risks.items())]), '',
        f"本批登记{summary['chart_count']:,}幅图；未逐页独立核验全范围年报／中报，未提出新经济参数自动选择器、评级或概率。", '',
        '## 全部证券列表', '', table(['分页', '证券范围', '行数'], pages), '',
        '[完整JSON与边界](results.json) · [完整CSV](results.csv) · [冻结研究协议](protocol.json)', '',
        '公司报告与事实、图表均在workspace，未提交项目仓库；同一证券前版不重复计数，缺项与非正结果不隐藏。', '']
    extras = protocol.get('extra_source_candidates', [])
    if extras:
        narrative += ['## 未进入当前消费快照的已解析源候选', '',
            '当前发布范围全部逐一覆盖；下列主库已解析源候选待消费快照发布，单列且不计本轮估值准入分母。', '',
            table(['证券／独立状态页', '状态'], [[f"[{r['name']}（{r['ticker']}）](/research/{r['report_id']})", r['status']] for r in extras]), '']
    (folder/'report.md').write_text('\n'.join(narrative))
    paths = [('report', '全部沪深公司覆盖总览', folder/'report.md'), ('results', '完整证券与状态结果', folder/'results.json'),
        ('csv', '完整CSV结果', folder/'results.csv'), ('protocol', '冻结范围与研究协议', protocol_file.resolve()), *page_files]
    files = [dict(id=identifier, label=label, path=path.relative_to(workspace_path()).as_posix(), sha256=digest(path))
        for identifier, label, path in paths]
    # 协议源文件保持唯一，正文链接由登记的文件ID解析，不复制冻结协议。
    narrative_text = (folder/'report.md').read_text().replace('(protocol.json)', '('+Path(os.path.relpath(protocol_file.resolve(), folder.resolve())).as_posix()+')')
    (folder/'report.md').write_text(narrative_text)
    files[0]['sha256'] = digest(folder/'report.md')
    metadata = dict(contract='alphalake-research-v1', id=folder.name, title=f"全部{len(results):,}家沪深初次覆盖：公司、条件值与缺项",
        summary=f"{admissions['ready']:,}家当前原生条件、{len(results)-admissions['ready']:,}家范围／输入状态；{summary['chart_count']:,}幅图，保留局部证据限制。",
        kind='research_note', status='complete', tickers=[], research_date=collection['acquired_at'][:10],
        financial_period=protocol['financial_report_period'], information_as_of=collection['acquired_at'], share_date=protocol['financial_report_period'],
        run_ids=[summary['run_id']], files=files, limitations=[
            '完整证券分母的结构化初次覆盖；不是逐页原文审计或经认证的新预测。',
            '统一沿当前冻结原生条件，旧人工故事只保留前版关联；没有默认参数变更、概率或自动采用。',
            '完整本地API重放与真实服务HTTP抽样验收分开；经营来源缺项、代理与异常结果继续保留。'])
    if revision:
        metadata['previous_id'] = base_folder.name
    verify_inputs(protocol, runtime=True)
    if digest(protocol_resource(financial, 'company_facts_file')) != financial['company_facts_sha256']:
        raise ValueError('发布总览前逐公司研究事实变化')
    report = publish(folder, metadata)
    for entry in report.files:
        verified_file(report, entry.id)
    save(work/'final-delivery.json', {k: v for k, v in summary.items() if k != 'results'} | dict(report_id=report.id, url='/research/'+report.id))
    return dict(report_id=report.id, count=len(results), calculated=admissions['ready'], charts=summary['chart_count'], url='/research/'+report.id)
