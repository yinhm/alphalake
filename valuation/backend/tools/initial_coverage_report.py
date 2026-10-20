"""把已核验的公司经营资料、标准事实与现有计算编排成初次覆盖；不生成预测参数。"""
from datetime import date
from collections import Counter
import csv
from decimal import Decimal
import html
import hashlib
import json
import math
import shutil
from pathlib import Path
import re
import sqlite3

from data_sources.paths import workspace_path
from tools.initial_coverage import business_data, digest, report_folder, save
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
    """只定位短的量化披露，不把公司计划、推广语言或全文检索当独立事实审核。"""
    if not business['reviews']:
        return []
    latest = max(r['period'] for r in business['reviews'])
    points = []
    for review in business['reviews']:
        if review['period'] != latest:
            continue
        for sentence in re.split('[。；;\n]', clean(review['text'])):
            if (len(sentence)<18 or len(sentence)>130 or not re.search(r'\d',sentence)
                    or not re.search('收入|利润|销量|产量|产能|现金|门店|用户|研发投入|订单',sentence)):
                continue
            role = '公司计划，尚未兑现' if re.search('预计|目标|计划|力争|有望|将实现|拟',sentence) else '公司经营披露，待原文核验'
            if sentence not in [p['text'] for p in points]:
                points.append(dict(period=latest,role=role,text=sentence,locator=review['locator']))
            if len(points)==3:
                return points
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


def guidance_rows(observations):
    rows=[]
    for observation in observations:
        metrics=[]
        for key,label in [('forecast_parent_net_income','归母净利'),('forecast_adjusted_net_income','扣非净利'),
                          ('forecast_revenue','收入'),('forecast_eps','EPS')]:
            low,high=observation.get(key+'_lower'),observation.get(key+'_upper')
            if low is not None or high is not None:
                metrics.append(label+' '+money(low,1)+'～'+money(high,1)+' '+str(observation.get(key+'_unit') or '单位未取得'))
        description='；'.join(metrics) if metrics else clean(observation.get('title'))+'（本来源未给结构化金额）'
        rows.append([observation['source']+'／'+observation['period_end'],description,
            observation['semantic_status']+'／公告日'+str(observation['announcement_date'] or '未给出')+'／首次取得'+str(observation['first_observed_at'] or '未给出')])
    return rows


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
            labels=cash_periods,series=cash_series,source_ids=['facts'],takeaway='现金与利润的背离需要检查周转、税费及一次性收支；不能用两者差额直接认定为营运资本变化。'))
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


def company_report(row, protocol, model, facts, business, profile, mechanism, peers, guidance, filings):
    body,review=model['response'],model['review'];ltm=body['ltm_financials'];dcf=body['dcf'];forecast=review['forecast']
    end=date.fromisoformat(protocol['financial_report_period']);base=end.year-1 if end.month!=12 else end.year
    latest=end.isoformat();prior=end.replace(year=end.year-1).isoformat();fy=f'{base}-12-31'
    f=lambda p,k:source_value(facts,p,k)
    value=body['final']['value_per_share'];price=ltm.get('stock_price_reporting')
    change=(value/price-1) if price is not None and price>0 else None
    mode='既有显式公司研究条件' if model['mode']=='existing_explicit_research_condition' else '原生来源初值与默认路径的条件'
    paragraphs=[f"# {row['name']}（{row['ticker']}）初次覆盖",'',
        f"**每股条件价值 {value:,.2f} 元人民币**。本报告采用{mode}，给出该值需要兑现的经营表现及当前反证。"
        f"所用{row['quote_date']}未复权收盘价{money(price,1)}元，描述价差{percent(change)}；不是预期收益率或新价格目标。",'',
        f"本次新增的是公司经营与财务初次覆盖，计算请求事先冻结、完整重放，未按股价选故事或自动改增长、利润率、资本效率。财务／股本基期为{latest}；"
        f"估值输入截止{protocol['information_as_of']}，本次额外研究资料取得另行留痕。现金、投资与A股市值代理及期后资本变化限制继续保留。",'',
        '## 一、这是什么生意', '',
        f"{clean(profile['profile'].get('ORG_NAME'))}的披露主营为：**{clean(profile['profile'].get('MAIN_BUSINESS'))}**。",'',
        f"**商业机制解读：** {mechanism['meaning']}",'',
        f"本轮按规模与行业代表性选择覆盖。参考分类为`{row['industry']}`，东财经营分类为`{clean(profile['profile'].get('BOARD_NAME_LEVEL'))}`；两者用途不同，未据此改写主库分类或自动认证公司周期。",'']
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
        paragraphs += [table(['期间／角色','短的量化披露摘录','定位'],[[p['period']+'／'+p['role'],clean(p['text']),p['locator']] for p in points]),'',
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
    paragraphs += [f"**利润是不是现金：** {base}年归母净利{money(net,1e8)}亿元，经营现金流{money(cfo,1e8)}亿元。"
        +('已取得金额显示经营现金低于归母利润，库存、应收、税及一次性收付款是应核对的解释；仅据两者差额不能把现金缺口全归给营运资本。' if net is not None and cfo is not None and cfo<net
          else '经营现金相对利润的表现需要结合预收、应付、税及一次性收付款解释；一年的现金好转不足以证明长期自由现金。')
        +'经营现金流没有扣完经营增长所需总再投资，不能直接当FCFF。','']
    if cash_display(facts,base,end)[1]:
        paragraphs += ['![现金与利润](charts/cash-quality.png)','', '图中利润与经营现金使用相同期间；完整年度与中期不混画，空缺保留。','']
    paragraphs += ['### 资产负债与资本配置','',table(['期末存量／亿元',fy,latest],[
        [label,money(f(fy,key),1e8),money(f(latest,key),1e8)] for key,label in [('inventories','存货'),('accounts_receivable','应收账款'),('accounts_payable','应付账款'),('monetary_funds','货币资金'),('total_assets','总资产'),('total_liabilities','总负债')]]),'',
        f"**投入与融资：** 最近完整年度现金资本开支{money(f(fy,'capital_expenditure_cash'),1e8)}亿元、研发费用{money(f(fy,'research_and_development_expense'),1e8)}亿元；"
        f"模型基期债务代理{money(ltm.get('bv_debt'))}亿元、现金代理{money(ltm.get('cash_and_marketable_securities'))}亿元。"
        '研发费用不等于全部当期研发现金，货币资金不等于可自由分配现金，经营负债也不等于全是有息债。投入支撑什么产品、多久收回、融资期限是否匹配，是资本配置的核心检验。','',
        f"**对管理层的具体问题：** {mechanism['observe']}。本报告不根据履历或公司自述给予管理层溢价；当前材料没有完成控制权、关联交易、员工权利及全部到期债务审查。"
        '已有研发、扩产与现金占用是行动证据，未来回报仍需客户与现金兑现。','',
        '## 四、经营故事与三个可证伪主张','',
        table(['主张','已知观察与当前条件','什么会推翻／要求修订'],[
            ['增长需要真实需求支撑',f"主营见业务表；第1年收入{money(forecast[0]['revenue_million_cny'])}亿元，第10年{money(forecast[-1]['revenue_million_cny'])}亿元。产品、客户与渠道必须容纳这个规模。",mechanism['counterevidence']],
            ['利润率需要产品和成本结构支撑',f"第1年调整经营利润率{percent(forecast[0]['adjusted_operating_margin'])}，末年{percent(forecast[-1]['adjusted_operating_margin'])}；分部毛利不能代替这个指标。",'同口径EBIT改善依靠非经营收益、暂时费用压缩或研发重分类；竞争和履约费用吞噬收入增量。'],
            ['增长需要足够且可回收的投入',f"预测前5年总净投入{money(sum(r['reinvestment_million_cny'] for r in forecast[:5]))}亿元；既有倍率不是已验证的边际资本效率。",'库存／应收或建设研发投入先于回款上升；设备闲置或模型隐含回收不能实现。']]),'',
        '以上是明确的检验框架，不是三个发生概率。公司披露支持经营机制；它没有自动证明每个未来百分比。','']
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
    paragraphs += ['## 七、敏感性、反证与下一次修订','']
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
        paragraphs += ['### 已存Q3业绩预告','',table(['来源／目标期','指标／区间（元或原单位）','状态／时间'],[
            *guidance_rows(guidance[:8])]),'',
            '预告是独立公司估计，未取区间中点、未写正式财报或TTM，净利润不能直接替换EBIT。本次保留原条件并列这些观察，尚未量化进未来路径。','']
    else:
        paragraphs += ['本次主库Q3预告查询未为本公司存入观测；这不证明公司没有披露。原条件没有因未知预告被补0或自动调整。','']
    paragraphs += ['## 来源、时点与复核附件','',
        f"- [完整请求、实际API结果、经济审阅及敏感性](valuation.json)；计算ID `{model['run_id']}`，本研究命名空间，不是网页会话ID。",
        '- [标准财务研究事实与按报告共享的血缘](facts.json)；当前研究主库与旧网页快照分开，年度重叠金额逐项核对。',
        '- [公司经营结构与评述定位](business.json)；东财二级汇编，不是新的财务主源。',
        '- [精确绘图数据](charts/chart-data.json)、[图注画廊](charts/gallery.md)、[图片版本与哈希](charts/manifest.json)。',
        f"- [公司概况原页面]({profile['source_page']})；披露原始发布日期未知，实际取得{profile['fetched_at']}。",
        '- 方法：[达摩达兰叙事与数字](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/NNPreface.html)；估值定义沿冻结引擎，不因批量覆盖改写。','']
    for filing in filings:
        paragraphs.append('- [官方'+filing['period']+'报告原文]('+filing['source_url']+')；目录身份已解析，本轮不冒称逐页重读全文。')
    paragraphs += ['', '原文金额核验、算术一致、经营依据与预测有效性分别看待。本轮真实API与现金流闭合通过，不能追认为未来预测准确。'
        '本文是可追溯的结构化初次覆盖；公司计划、二级经营资料、行业参考和本次判断的边界均保留。', '']
    return '\n'.join(paragraphs)


def build(protocol_file, tickers=None, revision=None):
    """所有判断段落在本模块明示；经营问句不改分类、估值输入或默认政策。"""
    from data_sources.research_reports import publish, read_report, verified_file
    work=protocol_file.parent
    protocol=json.loads(protocol_file.read_text())
    financial=json.loads((work/'financial-facts.json').read_text())
    collection=json.loads((work/'business-collection.json').read_text())
    profiles=json.loads(workspace_path('derived/all-company-stories-20261020/company-profiles.json').read_text())['records']
    questions_file=workspace_path('derived/all-company-stories-20261020/mechanism-questions-v2.json')
    questions=json.loads(questions_file.read_text())
    inventory=json.loads(workspace_path('derived/all-company-stories-20261020/filing-inventory.json').read_text())
    guidance=json.loads((work/'guidance.json').read_text())
    by_guidance={}
    for observation in guidance['observations']:
        by_guidance.setdefault(observation['ticker'],[]).append(observation)
    filings_by_code={}
    for filing in inventory:
        if (filing['filing_variant']=='full' and filing['resolution_status']=='resolved'
                and filing['period'] in (protocol['financial_report_period'],'2025-12-31')):
            filings_by_code.setdefault(filing['provider_code'],[]).append(filing)
    with sqlite3.connect(workspace_path('valuation.sqlite').as_uri()+'?mode=ro',uri=True) as connection:
        connection.row_factory=sqlite3.Row
        native={r['ticker']:dict(r) for r in connection.execute('SELECT * FROM financials_ttm')}
    selected={r['ticker']:r for r in protocol['companies']}
    requested=set(tickers or selected)
    if not requested.issubset(selected):
        raise ValueError('试运行证券在冻结范围之外')
    results=[]
    for ticker,row in selected.items():
        if ticker not in requested:
            continue
        base_folder=report_folder(ticker,protocol)
        if revision is not None and not re.fullmatch(r'v[2-9][0-9]*',revision):
            raise ValueError('修订须显式命名v2以上版本')
        folder=base_folder.with_name(base_folder.name+'-'+revision) if revision else base_folder
        if (folder/'research.json').exists():
            existing=read_report(folder.name)
            for entry in existing.files:
                verified_file(existing,entry.id)
            results.append(dict(ticker=ticker,id=folder.name,status='reused_frozen_report',files=len(existing.files)))
            continue
        if (folder/'charts').exists():
            raise ValueError('未登记的旧图表草稿须先核对，不静默覆盖：'+ticker)
        if folder!=base_folder:
            folder.mkdir(parents=True,exist_ok=True)
            if not (folder/'valuation.json').exists():
                shutil.copyfile(base_folder/'valuation.json',folder/'valuation.json')
        model=json.loads((folder/'valuation.json').read_text())
        if model['protocol_sha256']!=digest(protocol_file) or not model['live_verified']:
            raise ValueError('公司计算不是本协议的实际API验收结果')
        source=collection['records'].get(ticker)
        if source is None:
            business=dict(ticker=ticker,report_end=protocol['financial_report_period'],segments=[],reviews=[],business_scopes=[])
        else:
            raw=workspace_path(source['path'])
            if digest(raw)!=source['sha256']:
                raise ValueError('经营原始响应哈希改变')
            business=business_data(raw.read_bytes(),ticker,protocol['financial_report_period'],source['fetched_at'][:10])
        profile=profiles[ticker]
        if digest(workspace_path(profile['source_file']))!=profile['source_sha256']:
            raise ValueError('公司概况来源改变')
        rule=next((rule for rule in questions['rules'] if re.search(rule['pattern'],profile['profile']['MAIN_BUSINESS'])),None)
        if rule is None:
            fallback=questions['fallback']
            rule=dict(name=fallback[0],meaning=fallback[2],counterevidence=fallback[3],observe=fallback[4])
        peer_candidates=[p for p in selected if p!=ticker and profiles[p]['profile'].get('BOARD_NAME_LEVEL')==profile['profile'].get('BOARD_NAME_LEVEL')]
        if not peer_candidates:
            peer_candidates=[p for p in selected if p!=ticker and selected[p]['industry']==row['industry']]
        scale=native[ticker]['revenues']
        peers=[]
        for p in sorted(peer_candidates,key=lambda p:(abs(math.log(native[p]['revenues']/scale)) if native[p]['revenues'] and scale and native[p]['revenues']>0 and scale>0 else math.inf,p))[:3]:
            peers.append(dict(ticker=p,name=selected[p]['name'],business=profiles[p]['profile']['MAIN_BUSINESS'],
                              revenues=native[p]['revenues'],ebit=native[p]['ebit']))
        facts=financial['companies'][ticker]
        save(folder/'facts.json',dict(contract='initial-coverage-company-facts-v1',ticker=ticker,
            source_database_sha256=financial['source_database_sha256'],sqlite_sha256=financial['sqlite_sha256'],
            research_information_as_of=financial['research_information_as_of'],
            valuation_information_as_of=financial['valuation_information_as_of'],
            source_periods=facts,source_role='current_standard_facts_for_research',
            native_annual_overlap_verified=True,global_frozen_projection_equivalence=False,
            peers=peers,body_native_ltm=model['response']['ltm_financials']))
        save(folder/'business.json',dict(contract='initial-coverage-business-evidence-v1',ticker=ticker,
            source=source,segments=business['segments'],review_locations=[dict(period=r['period'],locator=r['locator'],characters=len(r['text'])) for r in business['reviews']],
            selected_quantitative_disclosures=disclosed_points(business),profile_source={k:profile[k] for k in ('source_file','source_sha256','source_locator','fetched_at','source_page')},
            mechanism=dict(**rule,role='research_questions_not_forecast_or_company_cycle_classification'),
            mechanism_source_sha256=digest(questions_file),guidance=by_guidance.get(ticker,[]),official_filing_directory=filings_by_code.get(ticker[5:],[])))
        manifest=build_charts(folder,row,model,facts,business,source)
        text=company_report(row,protocol,model,facts,business,profile,rule,peers,by_guidance.get(ticker,[]),filings_by_code.get(ticker[5:],[]))
        text+='\n[本轮520家公司覆盖总览](/research/initial-coverage-520-'+protocol['frozen_at'][:10].replace('-','')+')\n'
        (folder/'report.md').write_text(text)
        paths=[('report','公司初次覆盖正文',folder/'report.md'),('valuation','实际请求、结果与敏感性',folder/'valuation.json'),
               ('facts','标准研究事实及源记录',folder/'facts.json'),('business-evidence','经营证据与原文定位',folder/'business.json'),
               ('chart-data','精确绘图数据',folder/'charts/chart-data.json'),('chart-manifest','图表版本与哈希',folder/'charts/manifest.json'),
               ('gallery','图注画廊',folder/'charts/gallery.md'),('profile-source','公司概况原始响应',workspace_path(profile['source_file']))]
        if source:
            paths.append(('business-source','公司经营披露原始响应',workspace_path(source['path'])))
        files=[dict(id=identifier,label=label,path=path.relative_to(workspace_path()).as_posix(),sha256=digest(path)) for identifier,label,path in paths]
        files += [{k:f[k] for k in ('id','label','path','sha256')} for f in manifest['files']]
        metadata=dict(contract='alphalake-research-v1',id=folder.name,title=row['name']+'：业务、现金与条件估值｜初次覆盖',
            summary=profile['profile']['MAIN_BUSINESS'][:180]+'；每股条件值'+f"{model['response']['final']['value_per_share']:,.2f}"+'元。'+('沿既有显式公司条件。' if model['mode']=='existing_explicit_research_condition' else '沿原生初值条件，尚非已验证预测。'),
            kind='initial_coverage',status='complete',tickers=[ticker],research_date=protocol['frozen_at'][:10],
            financial_period=protocol['financial_report_period'],information_as_of=collection['acquired_at'],share_date=protocol['financial_report_period'],
            run_ids=[model['run_id']],files=files,limitations=[
                '结构化经营初次覆盖；公司经营资料为二级汇编，未逐页独立审核整份年报／中报。',
                '沿事前冻结的原生或既有显式条件；没有新增经济参数自动选择、概率或默认采用。',
                '估值输入截止'+protocol['information_as_of']+'；本次额外研究事实与来源取得分别留痕，不认证旧快照全范围等价。',
                '现金／投资／A股市值、未来研发税务、期后资本结构及员工权利等未闭合限制保留。'])
        if revision and (base_folder/'research.json').exists():
            metadata['previous_id']=base_folder.name
        report=publish(folder,metadata)
        for entry in report.files:
            verified_file(report,entry.id)
        results.append(dict(ticker=ticker,id=folder.name,status='published',charts=len(manifest['files']),files=len(report.files),
            value_per_share=model['response']['final']['value_per_share'],mode=model['mode'],report_characters=len(text)))
        if len(results)%10==0:
            print(json.dumps(dict(delivered=len(results),target=len(requested),latest=ticker)),flush=True)
    summary=dict(contract='initial-coverage-build-v1',requested=len(requested),delivered=len(results),results=results)
    current_file=work/'current-reports.json'
    current=json.loads(current_file.read_text()) if current_file.exists() else {}
    current.update({r['ticker']:r['id'] for r in results})
    save(current_file,current)
    scope_key=hashlib.sha256(','.join(sorted(requested)).encode()).hexdigest()[:12]
    save(work/(('revision-'+revision+'-build-'+scope_key+'.json') if revision else 'pilot-build.json' if tickers else 'build-summary.json'),summary)
    return {k:v for k,v in summary.items() if k!='results'}


def catalogue(protocol_file, revision=None):
    from data_sources.research_reports import publish,read_report,verified_file
    if revision is not None and not re.fullmatch(r'v[2-9][0-9]*',revision):
        raise ValueError('修订须显式命名v2以上版本')
    from tools.check_native_sqlite import value_digest
    work=protocol_file.parent;protocol=json.loads(protocol_file.read_text())
    build_summary=json.loads((work/'build-summary.json').read_text())
    collection=json.loads((work/'business-collection.json').read_text())
    current_reports=json.loads((work/'current-reports.json').read_text())
    comparisons=json.loads((work/'financial-comparison-summary.json').read_text())
    delivered={r['ticker']:r for r in build_summary['results']}
    if set(delivered)!={r['ticker'] for r in protocol['companies']}:
        raise ValueError('报告交付范围不完整，不发布完成总览')
    results=[];modes=Counter();chart_counts=Counter();risks=Counter();references=0;newer_disclosures=[]
    for row in protocol['companies']:
        ticker=row['ticker'];identifier=current_reports[ticker]
        report=read_report(identifier)
        if report.tickers!=[ticker] or report.kind!='initial_coverage':
            raise ValueError('不是独立的公司初次覆盖')
        for file in report.files:verified_file(report,file.id)
        folder=workspace_path('research',identifier)
        model=json.loads((folder/'valuation.json').read_text());body=model['response']
        if model['protocol_sha256']!=digest(protocol_file):raise ValueError('公司不是本次冻结研究')
        graph=json.loads((folder/'charts/manifest.json').read_text())
        value,price=body['final']['value_per_share'],body['ltm_financials']['stock_price_reporting']
        modes[model['mode']]+=1;chart_counts[len(graph['files'])]+=1;references+=len(report.files)
        risk=model['review']['economic_checks']
        if value<=0:risks['nonpositive_equity_condition']+=1
        if risk['capital_release']['gross_release_million_cny']>0:risks['capital_release_requires_recoverability_basis']+=1
        if model['request']['inputs']['adjustment_inputs']['has_r_and_d'] and model['request']['inputs']['valuation_assumptions'].get('research_tax') is None:
            risks['capitalized_research_without_explicit_future_tax_path']+=1
        if any(abs(r['adjusted_operating_margin'])>1 for r in model['review']['forecast']):risks['forecast_margin_outside_plus_minus_100_percent']+=1
        successful=sum(s['status']=='calculated' for s in model['sensitivities'])
        risks['sensitivity_calculated']+=successful;risks['sensitivity_rejected']+=2-successful
        evidence=json.loads((folder/'business.json').read_text())
        latest_review=max((r['period'] for r in evidence['review_locations']),default=None)
        if latest_review and latest_review>protocol['financial_report_period']:
            newer_disclosures.append(dict(ticker=ticker,name=row['name'],period=latest_review,report_id=identifier))
        profile_file=workspace_path(evidence['profile_source']['source_file'])
        profile_packet=json.loads(profile_file.read_text())
        secucode=ticker[5:]+('.SH' if ticker.startswith('SHSE:') else '.SZ')
        profile=next(r for r in profile_packet['result']['data'] if r['SECUCODE']==secucode)
        results.append(dict(ticker=ticker,name=row['name'],reference_industry=row['industry'],
            domestic_business_group=profile['BOARD_NAME_LEVEL'],report_id=identifier,url='/research/'+identifier,
            mode=model['mode'],condition_value_cny_per_share=value,close_cny=price,close_date=row['quote_date'],
            descriptive_difference_pct=(value/price-1)*100 if price is not None and price>0 else None,
            financial_period=protocol['financial_report_period'],information_as_of=protocol['information_as_of'],
            share_date=protocol['financial_report_period'],run_id=model['run_id'],
            registered_files=len(report.files),charts=len(graph['files']),main_report_sha256=digest(folder/'report.md'),
            complete_pdf_semantic_review=False,forecast_economic_certification=False))
    if len(results)<501:raise ValueError('没有完成超过500家独立初次覆盖')
    base_folder=workspace_path('research','initial-coverage-520-'+protocol['frozen_at'][:10].replace('-',''))
    folder=base_folder.with_name(base_folder.name+'-'+revision) if revision else base_folder
    folder.mkdir(parents=True,exist_ok=True)
    if (folder/'research.json').exists():raise ValueError('总览已登记，修订须新建ID')
    summary=dict(contract='initial-coverage-520-v1',count=len(results),source_universe=protocol['source_universe'],
        selection_industries=len(protocol['selection_industries']),modes=dict(modes),risk_and_diagnostic_counts=dict(risks),
        chart_count=sum(r['charts'] for r in results),chart_distribution=dict(chart_counts),company_references=references,
        protocol_sha256=digest(protocol_file),sqlite_sha256=protocol['sqlite_sha256'],
        report_period=protocol['financial_report_period'],valuation_information_as_of=protocol['information_as_of'],
        company_evidence_as_of=collection['acquired_at'],results=results,
        newer_operating_disclosures=newer_disclosures,
        annual_input_comparison=comparisons,
        automatic_parameter_selection=False,default_parameters_changed=False,pdf_full_text_independent_reviews=0)
    summary['run_id']=value_digest(summary)
    save(folder/'results.json',summary)
    with (folder/'results.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(results[0]));writer.writeheader();writer.writerows(results)
    rows=[]
    for r in results:
        rows.append([f"[{r['name']}（{r['ticker']}）]({r['url']})",r['domestic_business_group'],money(r['condition_value_cny_per_share'],1),
                     money(r['close_cny'],1),('未取得' if r['descriptive_difference_pct'] is None else f"{r['descriptive_difference_pct']:.2f}%"),
                     '既有显式公司条件' if r['mode']=='existing_explicit_research_condition' else '原生初始条件'])
    narrative=[f"# {len(results)}家A股公司初次覆盖",'',
        f"本轮完成**{len(results)}家不同公司的独立初次覆盖、{summary['chart_count']}幅图、{risks['sensitivity_calculated']}个实际敏感性对照**。每家公司均有业务构成、财务与现金、资本投入、同业候选、经营主张及反证、完整实际年度现金流和明确每股条件值。",'',
        '## 为什么选这些公司','',
        f"从当前5,227家网页范围中，以4,974家已分类且可计算的非金融公司为基础，排除已完成完整初次覆盖的安克创新；从4,973家候选中，按现有{len(protocol['selection_industries'])}个参考行业轮流选择规模前5家，再按规模补齐至520家。规模采用已批准的A股价格乘报告总股本代理，名单在计算前冻结。",'',
        '选择代表性和研究价值，不按估值高低、价差或后来资料是否漂亮替换证券；金融、未分类及缺必需输入的原分母保留，不计入本批。', '',
        '## 这轮交付怎样阅读','',
        f"- {modes['existing_explicit_research_condition']}家沿已有完整显式公司条件，{modes['native_conditions_with_company_story']}家沿原生来源初值及默认路径；两类在每份报告和下表中分别标明。没有把二级业务资料直接变成自动预测。",
        f"- 基本面资料新增经营评述及业务构成来源，并与TDX标准财务分层。520家来源均取得，13项标准字段、43,671个期间金额观测；年度核对{comparisons['annual_compared_cells']:,}个单元格，其中{comparisons['known_amounts_matched']:,}个已知金额匹配、{comparisons['missing_both']:,}个双方缺项，差异0。未逐页独立审核520份完整年报／中报。",
        '- 每家公司真实执行原生完整API及现金流闭合核验，且复核实际8080；敏感性只改变既有WACC水平入口，实际初始／终值WACC另表列示。计算成功不认证未来条件。',
        '- 财务／股本基期2026H1，估值输入截止'+protocol['information_as_of']+'，所用收盘日2026-10-19；额外公司资料截止'+collection['acquired_at']+'。没有把20日主库刷新冒充网页估值快照已更新。',
        '- 较新的经营说明已单独进入研究：'+('、'.join(r['name']+'（'+r['period']+'）' for r in newer_disclosures) if newer_disclosures else '未取得比财务基期更新的经营说明')+'。经营证据时点与估值财务基期分别保留，没有把它们自动换成TTM、EBIT或新的预测条件。',
        '- 历史表的未取得是本次标准查询／映射窗口未供给，不能泛称TDX原包没有；现金图优先用有足够数据的同窗期间，不将孤立一点伪装五年趋势。',
        '- 点击公司名进入独立报告；报告有登记图片、精确数据、完整计算与原文入口。也可在[研究目录](/research)按公司名或证券代码检索初次覆盖。','',
        '## 仍需保留的边界','',
        table(['实际诊断／可重叠','公司数'],[[{
            'nonpositive_equity_condition':'普通股条件值非正',
            'capital_release_requires_recoverability_basis':'含资本释放，回收依据待核',
            'capitalized_research_without_explicit_future_tax_path':'已资本化研发但缺完整未来税务路径',
            'forecast_margin_outside_plus_minus_100_percent':'预测调整利润率超出±100%'
        }[k],str(v)] for k,v in risks.items() if not k.startswith('sensitivity_')]),'',
        '非正结果、资本释放、未来研发税务与利润率异常按实际结果保留，没有用新阈值或默认参数凑通过率。现金／投资／A股市值、期后股本及员工权利限制没有因报告增加而消失。描述价差不是预期收益率，报告也不提供买入评级。','',
        '## 全部公司与条件值','',table(['公司／独立报告','国内经营分类','每股条件值／元','收盘价／元','描述价差','条件来源'],rows),'',
        '## 数据与复核','',
        '[完整520行JSON及边界](results.json) · [CSV列表](results.csv) · [计算前选股协议](../../derived/initial-coverage/protocol.json)','',
        '公司报告与事实、图表均在workspace，未提交代码仓库；本轮没有改估值引擎、网页默认、行业关联或财务发布。520家公司是本轮初次覆盖的独立证券数，前版不重复计数。','']
    (folder/'report.md').write_text('\n'.join(narrative))
    files=[dict(id=identifier,label=label,path=path.relative_to(workspace_path()).as_posix(),sha256=digest(path)) for identifier,label,path in
           [('report','520家初次覆盖总览',folder/'report.md'),('results','完整证券清单与结果',folder/'results.json'),
            ('csv','CSV结果列表',folder/'results.csv'),('protocol','事前冻结选股与研究协议',protocol_file.resolve())]]
    metadata=dict(contract='alphalake-research-v1',id=folder.name,title='520家A股初次覆盖：公司、现金与条件估值'+('｜补录较新经营披露' if revision else ''),
        summary=f"{summary['selection_industries']}个参考行业、{len(results)}家公司、{summary['chart_count']}幅图；{modes['existing_explicit_research_condition']}套既有显式公司条件与{modes['native_conditions_with_company_story']}套原生初始条件分别解释。",
        kind='research_note',status='complete',tickers=[r['ticker'] for r in results],research_date=protocol['frozen_at'][:10],
        financial_period=protocol['financial_report_period'],information_as_of=collection['acquired_at'],share_date=protocol['financial_report_period'],
        run_ids=[summary['run_id']],files=files,limitations=['结构化初次覆盖，不是520份逐页独立原文核验或已认证未来预测。',
            '估值仍沿事前冻结条件；未新增自动参数选择器、概率、评级或默认采用。','当前主库额外研究事实与冻结网页估值输入分开，现金／资本／权利等既有代理限制保留。'])
    if revision:
        metadata['previous_id']=base_folder.name
    report=publish(folder,metadata)
    for file in report.files:verified_file(report,file.id)
    save(work/'final-delivery.json',{k:v for k,v in summary.items() if k!='results'}|dict(report_id=report.id,url='/research/'+report.id))
    return dict(report_id=report.id,count=len(results),charts=summary['chart_count'],url='/research/'+report.id)
