# 跨公司可重复的条件估值方法

本轮交付单位是统一规则、完整输入及可重放结果，不是某家公司的目标价。沿用`tools.evaluate_native_policy`，提供SQLite、参考包、统一配方及证券列表即可运行，不需要逐公司编写政策。原生网页默认假设不变。

## 规则及理论边界

方法依据：[达摩达兰增长、利润率与再投资](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/littlebook/growthvaluedrivers.htm)、[终值增长与再投资](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/termvalueexreturns.htm)。具体观察窗口、过渡年数和参考准入参数是本项目固定政策，不冒充教材规定。

| 环节 | 统一处理 | 不代表什么 |
|---|---|---|
| 基期与调整 | 已发布SQLite→原生年度/季度输入→共享引擎TTM与研发等调整；完整输入保存 | 数据能计算不等于现金/投资代理或调整寿命获经济认证 |
| 增长起点 | 最新可比YTD同比；年度基期取完整年度同比，缺项不改用更早年度 | 历史同比不是预测事实 |
| 持续期 | 首年取统一起点，分别于第3/5/10年线性收敛至同一稳定增长率，之后保持 | 无默认胜出者，无概率，不把十年衰减当合理性的证明 |
| 当前利润情景 | 调整后TTM利润率持平 | 正利润不能证明持续性 |
| 历史利润情景 | 最近连续三完整年度调整后EBIT合计/收入合计；从当前利润率到第5年线性收敛 | 只是收入加权均值回归情景，不是自动正常化或竞争优势判断 |
| 历史口径门槛 | 每年研发队列完整且年度唯一、收入正；另有租赁资本化或prepared TTM调整时不直接混入仅研发调整历史 | 不用未调整同行利润率填历史缺口，不压缩年度 |
| 资本需求 | 同一Global行业销售资本代理，按原引擎投入滞后计算净再投资；逐年拆解增量NOPAT来源与隐含回报 | 行业存量比不是公司新增资本效率；不重复扣研发或营运资本 |
| 折现与终值 | 使用显式人民币参考WACC场景；增长上限和终值ROIC=WACC沿用基础选择器；终值再投资联动 | 全中国暴露、BBB信用档、行业权重及统一税盾仍是代理 |
| 准入与输出 | 缺项、收缩、亏损、极端增长按基础选择器保留拒绝；历史目标缺项不删除可算的持平场景 | 拒绝是此规则的能力边界，不是达摩达兰不能估值此类公司 |

`native-evidence-scenarios-v1`固定3个增长持续期×最多2个利润率目标。除逐年增长/利润路径外，完整所选输入不变；税率路径单独沿用，不通过改高增长阶段长度联动税率/WACC。末年税率累加舍入按同一终值税率对齐，不改变税收假设。

`evidence_selected`及原`discount_only`、`joint_candidate`仍是具名比较基线，不是兼容别名或推荐结果。`scenario_generation.selected_scenario=null`，没有按输出大小选中间值、均值或最贴近股价的情景。当前方法可以重复产生有依据的条件输入，尚未证明预测有效性。

## 一步预测诊断

同一入口对最后两个完整年度运行四组固定对照：最近年度同比/三年CAGR × 上一年度调整利润率/此前三年收入加权调整利润率。预测起点只使用此前年度；不将后来实际值带入预测，不删除亏损实际值，不按误差筛公司或扫描窗口。

每条记录保留起点、目标年度、实际、预测及缺项。收入误差按目标收入缩放；调整后EBIT误差也按目标收入缩放，避免亏损或近零EBIT分母失真。每项指标在四规则共同可评分交集比较，先公司内平均再公司间平均；同时公开完整公司×期间分母，不能把多期当成独立公司。

这是当前历史数据版本的简化回溯，后下载包修订仍可能导致结构性前视；既有样本已被研究，不声称独立留出、严格PIT、全市场代表性或完整DCF精度。一步诊断不能选出3/5/10年持续期，也不据其结果自动替换默认政策。历史目标在情景中渐变到达，诊断的直接一步目标只是规则比较，不能混称完整情景回测。

## 使用和重放

在已核验MemoryMax的独立systemd服务中串行运行：

```bash
PYTHONPATH=valuation/backend .venv/bin/python -m tools.evaluate_native_policy \
  --database workspace/derived/valuation.sqlite \
  --references workspace/derived/native-policy/final-five/references.json \
  --recipe valuation/examples/native-cny-candidates.json \
  --selection-policy valuation/examples/native-assumption-selection.json \
  --ticker SHSE:600519 --ticker SZSE:002032 \
  --output workspace/derived/uniform-scenarios-new \
  --web http://127.0.0.1:8080

PYTHONPATH=valuation/backend .venv/bin/python -m tools.review_native_policy \
  workspace/derived/uniform-scenarios-new \
  workspace/derived/uniform-scenarios-new-review.json
```

引用既有本地参考包不代表上游最新；参考时点、过期门槛和财务时点独立记录。输出目录必须新建，保存计算前protocol、SQLite/参考/运行时标识、每家公司基线/请求/审计/结果/经济审阅和预测诊断。`summary.json`给出全名单状态、各场景规模/再投资/条件值/终值占比及同分母误差汇总。完整历史输出按原提交复验；当前审阅要求当前诊断契约，不兼容旧目录。

事实、参考代理、分析者规则在`scenario_generation.input_classes`分别标注。真实API核对输入、DCF、最终结果和参考版本；目录重放还重建选择、情景和诊断，防止仅重算被改写的假设就称为原规则通过。

后续[研发队列桥接](../history/native-research-cohort-bridge.md)已修复缺利润行丢失有效研发的问题，恢复3个模型历史利润率；原12家场景及DCF不变，历史四规则配对仍不足。以下为首轮验收。

历史样本数值、运行标识及测试收据见[验收记录](../history/native-uniform-scenarios.md)；当前交付数量统一见[项目状态](../implementation-status.md)。
