# 原生估值的人民币政策与经营候选

现可通过同一入口的显式选择政策自动生成条件假设，增长与资本参考不再只能使用原CAGR对照；规则、边界及当前五家验收见[自动选择](native-assumption-selection.md)。原网页默认保持不变。

本轮沿用原生SQLite和`POST /api/valuation`完整输入接口，不新增前端、第二套引擎或公司事实。原网页默认不变。目标是将现有WACC解析接入原生输入，并固定财务后检验经营假设的现金流传导，同时继续批量补供给。

## 方法与边界

依据Damodaran的[成本资本说明](https://pages.stern.nyu.edu/~adamodar/pdfiles/blog/CostofCapitalInPractice.pdf)，无风险利率须与现金流币种一致，本轮显式选择人民币十年国债减中国主权违约利差。权益成本沿用既有成熟市场ERP加国家风险分量计算；Global行业去杠杆Beta与D/E、100%中国风险暴露、BBB信用档、25%税盾均列为政策代理，不称公司市场WACC。US仍是原网页默认，Global只用于这份明确选择的政策。

依据[增长与再投资](https://pages.stern.nyu.edu/adamodar/New_Home_Page/valquestions/growth.htm)及[终值再投资](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/termvalueexreturns.htm)，收入增长须与资本投入配套，终期再投资率为增长/资本回报。以下历史增长和行业倍率是待审候选，方法依据不等于预测参数获得验证。

固定配方见[`native-cny-candidates.json`](../../valuation/examples/native-cny-candidates.json)：

- 原生默认：保持已发布SQLite和原模型参数。
- 仅折现率对照：用已有参考WACC分支替换初期及终期折现率，显式保留原增长、利润率、再投资和终值ROIC。用于隔离折现影响；原高终值增长可能接近新WACC，因此不能直接作为人民币估值建议。
- 联合候选：四个连续年度收入计算三年CAGR、调整后TTM利润率保持、US行业历史销售资本倍率作为增量资本代理；终值增长取2%与人民币无风险率较低者，终值ROIC=WACC。缺收入年度不压缩、不归零；极值不裁剪；缺资本倍率拒绝此候选。历史收入直接消费SQLite标准事实，不要求同年EBIT同时存在。

Global WACC同行与US资本效率同行不是同一个样本口径，两者分别留痕；这里提供可替换的条件候选，未证明其联合经济合理性。已有零增长跨期、利润校准及资本开支候选失败结论保持，不再扫描阈值；当前没有足够证据将这一候选自动设为全市场默认。原生现金/长期投资、债务及多股类市值代理边界照旧。

## 接口与执行

`tools.evaluate_native_policy`读取SQLite、主库导出的WACC参考及显式配方，先写固定样本和版本协议，再经原生API输出每家公司：默认结果、完整请求、政策/源值/请求及实际输入差异、条件结果和拒绝原因。每份成功候选另存[经济输入审阅](../history/native-policy-economic-review.md)，经输入重放核对后列出经营和资本需求；既有目录可独立补审阅。可选`--web`逐项核对8080的完整输入API；不修改网页快照。完整请求可直接提交`POST /api/valuation`，无需新增API或前端JSON入口。工具输出目录必须是新的派生目录，不覆盖既有验收。

```bash
./alphalake export-wacc-references workspace/alphalake.duckdb \
  --as-of <参考截止RFC3339> --latest > workspace/derived/native-policy/references.json
PYTHONPATH=valuation/backend .venv/bin/python -m tools.evaluate_native_policy \
  --database workspace/derived/valuation.sqlite \
  --references workspace/derived/native-policy/references.json \
  --recipe valuation/examples/native-cny-candidates.json \
  --ticker SZSE:300866 --ticker SHSE:600519 \
  --output workspace/derived/native-policy/new-run --web http://127.0.0.1:8080
```

本机操作须串行放入已核验1GiB硬限额的独立systemd服务。金融分类、币种不匹配、参考过期、报告期/快照失配均拒绝；WACC参考截止和财务截止分别记录，不能冒称严格历史PIT。工具的参考包需来自可信主库，结构校验不是数据库数字签名。

WACC范围改为由调用链传入：原生为合并口径，茅台专项仍为酒业口径；不按证券代码替调用者决定会计范围。合成评级/市场债务分支需要独立已审核证据，当前原生候选拒绝这两种输入，使用显式信用档情景。

## 验收纪律

同一财务/股本/资产桥接固定后比较政策，保留输入变化、现金流和终值占比；高估值或更贴近股价不构成通过条件。回归独立Decimal复算WACC及终值公式，验证仅折现路径不改变预测现金流；负向覆盖错币种、过期参考、错时点、断年收入及错误范围。真实验收与供给补采结果完成后在本页记录，不以测试替代公司原文核验。

`company_metrics.cost_of_capital`是引擎回填的派生量，不是新增源事实；请求差异与计算后输入差异分开保存。零经营资产价值的终值占比保持空值，不为计算占比而除零或删除该公司。

历史样本数值、运行标识及测试收据见[验收记录](../history/native-cny-policy.md)；当前交付数量统一见[项目状态](../implementation-status.md)。
