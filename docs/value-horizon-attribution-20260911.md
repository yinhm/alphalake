# 估值价值来自哪些预测期间

本轮对固定五家既有基线做价值分解：安克、苏泊尔两份标准请求由当前共享引擎完整报告精确重放，格力、伊利、海天三个原阻断继续保留。没有重新查询主库、更新公司输入或产生新估值；财务期2026H1、信息截止2026-09-10T04:42:23Z、行业机械政策。详细运行哈希、选定股本与分量见[回执](acceptance/value-horizon-attribution-20260911.json)。

| 经营资产价值构成 | 安克 | 苏泊尔 |
| --- | ---: | ---: |
| 首年FCFF现值 | 0.22% | 4.87% |
| 第2—5年FCFF现值 | 1.14% | 16.71% |
| 第6—10年FCFF现值 | 16.97% | 15.08% |
| 终值现值 | 81.67% | 63.34% |

分母为经营资产价值，不是股权价值或当前市值；四项原始未舍入值加总为100%。回执另将各分量换算为每股贡献，加上所选股权桥接后，分别回到原126.1378元与43.2526元条件值。股本取共享股权桥接实际选择的分母，包含已有稀释政策，不借用财报原始股本；这两例期权价值为零，验证时显式断言，没有把其他公司也当零。

## 对研究优先级的影响

安克前五年FCFF合计仅占经营资产价值约1.36%，苏泊尔约21.58%。安克首年政策FCFF约1.67亿元，第十年约83.28亿元：早期20%增长需要较多再投资，后期增速下降减少资本需求，现金流才明显释放。这是当前政策的模型结果，不是实际现金流已经验证，也不代表高终值占比本身就是程序错误。

在后续全部现金流、WACC、股权桥接、转股分支与股本均固定的局部算式下，首年FCFF每相差1亿元，安克每股条件值约相差0.1692元，苏泊尔约0.1159元。它只是单位现金扰动的价值传导，不是实际预测误差、估值上下界或改进收益；首年增长变化若持续影响未来收入，会同时改变其他期间，不能沿用这个局部系数推断完整影响。

因此，最近的收入/利润/OCF一年候选即使在个别窗口改善，也没有验证大部分价值来源。后续暂停追加只有同类历史增长统计量的新候选，优先检验**多年增长所需的投入资本与现金释放路径**：先在固定公司上核对可追溯的资本效率依据，再检查增长减速时的再投资和终值回报是否有业务依据。完整经营资本尚缺分类时，保留显式政策与缺口，不能把行业均值或为衔接而反推的比率称为公司实测回报。

现有两年/三年经营路径诊断和终值ROIC等于WACC的对照继续使用；不因本分解否定已有一年验证，也不把多年假设敏感性称为预测准确度改善。本轮没有推广失败规则或改变任何生产估值。

## 本地复验

逐年用Decimal复算FCFF×折现因子，按原报告分组核对经营价值及含桥接每股加总；在内存中把首年现值改动100万元，既有重放入口必须拒绝。原归档文件不变。复验依赖本地保留的原run及已有后端Python环境，**不声称裸克隆CI覆盖这两份本地运行**；本轮仅文档/派生回执，无生产代码、依赖或数据变更，不重复Go全套。

```bash
PYTHONPATH=valuation/backend workspace/anker-agent-adapter-20260906/venv/bin/python - <<'PYTHON'
from copy import deepcopy
from decimal import Decimal as D
import json, math
from pathlib import Path
from tools.compare_valuations import load_run, replay
baseline = json.loads(Path('docs/acceptance/valuation-baseline-five-20260911.json').read_bytes())
rows = []
for company in baseline['companies']:
    row = dict(code=company['code'],name=company['name'],status=company['status']); rows.append(row)
    if 'run_id' not in company:
        row['reason'] = company['reason']; continue
    run, evidence = load_run('valuation/backend/data/alphalake_runs',company['run_id'])
    assert evidence['sha256'] == company['saved_run']['sha256']
    report, inputs = replay(run); dcf = report['dcf']; bridge = report['equity_bridge']
    assert report['final']['value_of_all_options'] == 0
    shares = bridge['shares_if_converted' if bridge['selected_case']=='converted' else 'shares_no_conversion']
    ownership = bridge['operating_ownership']; ev = dcf['value_of_operating_assets']
    assert len(dcf['pv_fcff']) == len(dcf['fcff_projections']) == len(dcf['discount_factors']) == 10
    for cash, discount, pv in zip(dcf['fcff_projections'],dcf['discount_factors'],dcf['pv_fcff']):
        assert math.isclose(float(D(str(cash))*D(str(discount))),pv,rel_tol=1e-12,abs_tol=1e-9)
    groups = {'year_1':dcf['pv_fcff'][0],'years_2_5':sum(dcf['pv_fcff'][1:5]),'years_6_10':sum(dcf['pv_fcff'][5:]),'terminal':dcf['pv_terminal_value']}
    assert math.isclose(sum(groups.values()),ev,rel_tol=1e-12)
    net_bridge = dcf['value_of_equity']-ev*ownership
    attribution = {k:dict(pv_million_cny=v,share_of_operating_value=v/ev,per_share_cny=v*ownership/shares) for k,v in groups.items()}
    assert math.isclose(sum(x['per_share_cny'] for x in attribution.values())+net_bridge/shares,report['final']['value_per_share'],rel_tol=1e-12)
    row.update(run_id=company['run_id'],saved_run=evidence,report_period=baseline['report_period'],information_as_of=baseline['information_as_of'],
        components=attribution,net_equity_bridge_million_cny=net_bridge,net_equity_bridge_per_share_cny=net_bridge/shares,
        selected_case=bridge['selected_case'],selected_shares_million=shares,operating_ownership=ownership,value_per_share_cny=report['final']['value_per_share'],
        first_year_fcff_million_cny=dcf['fcff_projections'][0],last_year_fcff_million_cny=dcf['fcff_projections'][-1],
        first_year_fcff_100million_cny_local_value_effect=100*dcf['discount_factors'][0]*ownership/shares)
    bad = deepcopy(run); bad['report']['dcf']['pv_fcff'][0] += 1
    try: replay(bad)
    except ValueError: pass
    else: raise AssertionError('tampered PV accepted')
result = dict(scope='fixed_five_existing_standard_runs_two_replayed_three_blocks_retained',companies=rows,
    boundary='PV allocation is not forecast accuracy or an error bound; one-year cash derivative holds all subsequent cashflows, WACC, ownership, shares, conversion branch and bridge fixed; no new valuation or adoption')
path = Path('docs/acceptance/value-horizon-attribution-20260911.json')
encoded = json.dumps(result,ensure_ascii=False,indent=2)+'\n'
if path.exists(): assert path.read_text() == encoded
else: path.write_text(encoded)
for row in rows:
    if 'components' in row: print(row['code'],{k:round(v['share_of_operating_value']*100,4) for k,v in row['components'].items()},'CNY/share per 100million first-year cash',row['first_year_fcff_100million_cny_local_value_effect'])
PYTHON
```
