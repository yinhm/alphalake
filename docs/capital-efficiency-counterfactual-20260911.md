# 资本效率来源与单因素估值对照

本轮聚焦正常经营下增长需要投入多少资本，不扩展疫情或异常经营个案。结论是资本效率代理对现金路径有显著影响，尚未证明替代代理更准确，也未改变生产政策。

## 原表与采用边界

安克、苏泊尔两份[固定基线](valuation-accuracy.md#五家公司完整基线盘点2026-09-11)都使用达摩达兰全球行业表的 `Sales/ Invested Capital (LTM)`，分别为计算机及外设2.483535697458、家具及家居2.27300234984。[原表及来源说明](../internal/source/damodaran/testdata/capexGlobal.README.md)和[本轮回执](acceptance/capital-efficiency-counterfactual-20260911.json)记录文件哈希、单元格、样本量和采用政策。

这是历史收入与投入资本存量之比；引擎将它显式采用为未来新增收入所需净再投资的代理，二者不是天然等价。达摩达兰强调增长同时取决于再投资数量及新增投资的回报，历史回报不自动代表未来边际回报；研发和租赁也须采用一致口径。参见[增长与再投资方法](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/growth.htm)。当前源表资本化口径与公司输入尚未完全调和，不能声称已经证明重复计算或公司实际资本效率。

## 安克资本效率单因素对照

财务期2026-06-30，信息截止2026-09-10T04:42:23Z，复用原标准链保存请求。原政策、行业分类及条件值不是本轮新估计。

[已核验的2024年报业务页](../valuation/research/damodaran-growth-history/anker-pilot/README.md)支持把“消费及办公电子”作为分析者的替代同行代理；不等于官方重分类或当前业务范围核验。该行业原表J36比率1.9058982485219271，122家公司。本轮只改变资本效率绑定，不更换WACC行业。

| 项目 | 原资本效率代理 | 替代同行代理 |
| --- | ---: | ---: |
| 收入/投入资本代理 | 2.483536 | 1.905898 |
| 首年FCFF（亿元） | 1.667654 | −6.692406 |
| 每股条件值（元） | 126.1378 | 111.4651 |

收入、经营利润、WACC、十年折现因子、终值及股权桥接保持不变。逐年以 Decimal 独立复算 `额外再投资 = 新增收入 × (1/替代比率 − 1/原比率)`，其现金现值之和与经营价值差额闭合。该公式验证计算传导，不验证代理的经济正确性；111.47元不是新目标价，亦非新采用结论。

下一步应核对正常经营公司的多年资本需求：已有资本与未来新增资本是否可比，研发/租赁、经营营运资本和净资本开支是否同口径，再检验增长减速及终值阶段的资本回报。优先使用既有源链和已核验历史，缺口继续列明，不为衔接反推“事实”，不再增加同类一年参数扫描。

## 可重复核验

下列脚本从仓库根目录执行，依赖既有后端环境及xlrd，读取原始XLS和两份本地保存运行，复用生产API生成独立反事实运行。校验来源单元格、完整报告重放、精确输入差异、重复运行幂等、逐年现金差额；内存篡改首年FCFF须被拒绝。它需要原基线运行存续，未接入裸克隆CI。本轮只增加文档和验收回执，未修改生产代码、依赖或主库，不重跑Go全套。

```bash
PYTHONPATH=valuation/backend workspace/anker-agent-adapter-20260906/venv/bin/python - <<'PY'
from copy import deepcopy
from decimal import Decimal as D
import hashlib, json, math, os
from pathlib import Path
import xlrd
from api.alphalake import evaluate
from data_sources.alphalake import AlphaLakeRequest
from tools.compare_valuations import load_run, replay, changes
source = Path('internal/source/damodaran/testdata/capexGlobal.xls')
meta = json.loads(source.with_suffix('.source.json').read_bytes())
assert hashlib.sha256(source.read_bytes()).hexdigest() == meta['sha256']
sheet = xlrd.open_workbook(source).sheet_by_name('Industry Averages')
assert sheet.cell_value(7,9) == 'Sales/ Invested Capital (LTM)'
base_list = json.loads(Path('docs/acceptance/valuation-baseline-five-20260911.json').read_bytes())
source_checks=[]
for row in base_list['companies']:
    if 'run_id' not in row: continue
    run,ev = load_run('valuation/backend/data/alphalake_runs',row['run_id'])
    assert ev['sha256'] == row['saved_run']['sha256']
    binding = run['request']['capital_binding']; industry=binding['policy']['industry']
    obs, = [o for o in binding['references']['observations'] if o['industry']==industry]
    assert binding['references']['release']['artifact_sha256'] == meta['sha256']
    cell=int(obs['source_locator'].split('!J')[1])-1
    assert sheet.cell_value(cell,0)==industry and sheet.cell_value(cell,1)==obs['sample_count']
    assert abs(D(str(sheet.cell_value(cell,9)))-D(obs['value'])) <= D('0.0000000000005')
    source_checks.append(dict(code=row['code'],industry=industry,observation=obs,adoption=binding['policy']))
run,evidence=load_run('valuation/backend/data/alphalake_runs',base_list['companies'][0]['run_id'])
base,base_inputs=replay(run)
request=deepcopy(run['request'])
patch=dict(industry='Electronics (Consumer & Office)',mapping_reason='2024年报消费电子业务作为资本效率同行替代敏感性；不变更WACC行业，不声称当前官方分类或公司边际效率。')
request['capital_binding']['policy'].update(patch)
os.environ['ALPHALAKE_VALUATION_RUN_DIR']='workspace/capital-efficiency-counterfactual-20260911/runs'
new=evaluate(AlphaLakeRequest.model_validate(request)); repeat=evaluate(AlphaLakeRequest.model_validate(request));assert new==repeat
counter,receipt=load_run(os.environ['ALPHALAKE_VALUATION_RUN_DIR'],new['run_id']);other,other_inputs=replay(counter)
allowed={'/prepared_ttm/provenance/assumption_rules','/prepared_ttm/provenance/capital_binding','/prepared_ttm/provenance/valuation_policy','/valuation_assumptions/sales_to_capital_high','/valuation_assumptions/sales_to_capital_stable'}
diffs=list(changes(base_inputs,other_inputs));assert {r['path'] for r in diffs}==allowed
for key in ('cost_of_capital','adjusted','ltm_financials','cashflow'):
    assert base[key]==other[key],key
for key in ('revenue_projections','ebit_projections','discount_factors','terminal_value_firm','pv_terminal_value'):
    assert base['dcf'][key]==other['dcf'][key],key
assert request['data']==run['request']['data'] and request['wacc_binding']==run['request']['wacc_binding']
observation=new['audit']['capital_reference']['observation'];i=int(observation['source_locator'].split('!J')[1])-1
assert sheet.cell_value(i,0)==patch['industry'] and sheet.cell_value(i,1)==observation['sample_count']
assert abs(D(str(sheet.cell_value(i,9)))-D(observation['value']))<=D('0.0000000000005')
s0=D(str(base_inputs['valuation_assumptions']['sales_to_capital_high']));s1=D(str(other_inputs['valuation_assumptions']['sales_to_capital_high']))
previous=D(str(base['ltm_financials']['revenues']))
deltas=[]
for year,revenue in enumerate(base['dcf']['revenue_projections']):
    increment=D(str(revenue))-previous;previous=D(str(revenue))
    delta=increment*(1/s1-1/s0)
    actual=other['dcf']['reinvestment_projections'][year]-base['dcf']['reinvestment_projections'][year]
    assert math.isclose(float(delta),actual,rel_tol=1e-11,abs_tol=1e-8)
    assert math.isclose(other['dcf']['fcff_projections'][year]-base['dcf']['fcff_projections'][year],-actual,rel_tol=1e-11,abs_tol=1e-8)
    deltas.append(dict(year=year+1,extra_reinvestment_million_cny=actual,pv_cash_change_million_cny=-actual*base['dcf']['discount_factors'][year]))
assert math.isclose(sum(r['pv_cash_change_million_cny'] for r in deltas),other['dcf']['value_of_operating_assets']-base['dcf']['value_of_operating_assets'],rel_tol=1e-11)
tampered=deepcopy(counter)
tampered['report']['dcf']['fcff_projections'][0]+=1
try: replay(tampered)
except ValueError as error: assert 'cannot exactly reproduce' in str(error)
else: raise AssertionError('tampered cash flow accepted')
result=dict(scope='capital_efficiency_only_counterfactual_not_adoption_or_price_update',source=meta,base_source_checks=source_checks,
    report_period=base_list['report_period'],information_as_of=base_list['information_as_of'],original_run=evidence,counterfactual_run=receipt,
    original_run_id=run['run_id'],counterfactual_run_id=new['run_id'],engine_revision=new['engine_revision'],request_patch=patch,input_changes=diffs,
    original_ratio=float(s0),counterfactual_ratio=float(s1),counterfactual_observation=observation,
    original_value_per_share=base['final']['value_per_share'],counterfactual_value_per_share=other['final']['value_per_share'],
    original_first_fcff_million_cny=base['dcf']['fcff_projections'][0],counterfactual_first_fcff_million_cny=other['dcf']['fcff_projections'][0],
    annual_cash_changes=deltas,boundary='2024 business evidence supports analyst alternative peer proxy only; no official remapping or company marginal-capital estimate; WACC/growth/margins/terminal/bridge fixed; RD and other accounting scope not reconciled; not forecast validation')
p=Path('docs/acceptance/capital-efficiency-counterfactual-20260911.json');encoded=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
if p.exists():assert p.read_text()==encoded
else:p.write_text(encoded)
print(new['run_id'],result['original_value_per_share'],result['counterfactual_value_per_share'])
PY
```
