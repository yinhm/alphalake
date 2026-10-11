---
name: alphalake-dcf
description: 完全使用 AlphaLake 已有数据，为沪深非金融公司完成可复验的 DCF 条件估值、逐年现金流分析、人民币 WACC 核对、敏感性分析及中文报告与 Excel 交付。适用于详细 DCF 建模、经营论点到估值、假设对照和模型审阅；不用于数据同步、一般股价预测、金融专项估值或修改估值引擎。
license: Apache-2.0，完整文本见 LICENSE.txt
---

# AlphaLake DCF 建模与交付

把 AlphaLake 的事实、参考和显式条件交给现有 valuation 引擎，交付可追溯的现金流、估值和中文解释。默认同时保存 JSON、中文摘要和原生 Excel **结果快照**；现金流计算只有共享引擎一套。Excel 内直接编辑并联动重算尚未实现，不能把结果快照称为独立可编辑模型。

## 环境与职责

- 默认 Linux；定位会话已知仓库或 `ALPHALAKE_ROOT`，核对 `valuation/backend/engine/orchestrator.py` 存在。Skill 安装目录不是项目根目录，不硬编码本机路径。
- 首次读取项目 `AGENTS.md`、`README.md`、`docs/implementation-status.md`，再读[数据契约](../../docs/valuation/data-contract.md)与[方法契约](../../docs/valuation/methodology-contract.md)。从项目根目录运行 `.venv/bin/python`，已确认其他解释器时使用 `ALPHALAKE_PYTHON`；设置 `PYTHONPATH=valuation/backend`。不另建 Python 环境或安装通用 DCF 依赖包。
- 复用标准库 `json/pathlib/hashlib/sqlite3/urllib` 和项目已有 `openpyxl`、typed 输入、原生 API、审阅及导出器；不需要 Excel 桌面、Office JS 或 Windows。批量计算、大库导出按[资源隔离约定](../../docs/guides/fundamental-memory-20260919.md)在独立 1GiB 服务串行执行。
- 普通估值查询和历史比较用项目 `skills/alphalake-valuation/SKILL.md`；数据更新用 `skills/alphalake/SKILL.md`。本 Skill 承担详细 DCF 分析与交付，不隐式刷新、发布、扩采或改引擎。
- 已确认的数据和条件直接推进到计算、核验与交付，每阶段报告有意义的发现；不逐表重复要求确认。仅缺少决定结果的必要信息、存在无法裁决的冲突或需要未获批方法变更时询问。

## 1. 完全从 AlphaLake 取得输入

唯一主库为 `workspace/alphalake.duckdb`；正常估值消费同版 `workspace/valuation.sqlite` 及其来源归档。先核对发布收据、证券身份、报告期、信息截止、报价日、股本基期和参考快照 ID，保存输入及文件 SHA-256。已有公司报告、研究论点和故事可从 `workspace` 读取，必须能够追溯 AlphaLake 已登记来源与具体定位。

| 需要的输入 | 既有取数入口与边界 |
|---|---|
| 年度、累计、TTM及标准财务 | `GET /api/database/company/{ticker}`；`POST /api/valuation/from-database` 用 `{ "ticker": "SHSE:688099" }` 取得完整原生基线，示例代码不作为默认公司 |
| 实际模型缺项与准入 | `GET /api/database/compatibility/{ticker}`；区分源缺失、未同步、未导出、未关联与所选模型不适用 |
| 人民币利率与达摩达兰参考 | `GET /api/database/wacc-references` 及基线的 `reference_snapshot/source_metadata`；只消费发布绑定版本，不读取另一份参考文件顶替 |
| 原文与经营依据 | 已归档年报、中报、预告及已有 thesis/story；引用期间、页码或表项、文件哈希。预告不填正式财务或EBIT，不自动取区间中点 |
| 接口为空但主库可能有值 | 沿标准宽表或 `export-financial-snapshot --help` 限定证券、期间和通用字段查询；发现适配缺口后留诊断，不在研究脚本手工补造原生事实 |

不从 SEC、行情网站、分析师接口、外部 MCP 或新下载 PDF 取公司数据；网络只可访问已确认的 AlphaLake API。方法依据查项目契约所引用的原始教材，方法资料不作为外部公司数值来源。外部事实需先走 AlphaLake 数据接入和发布，不能在 DCF 请求里直接注入。用户输入仅作为显式预测或已支持的政策条件，单独标明来源，不覆盖有效财务事实。

**单位和期间必须先核对：**

- 标准财务为人民币元／股数为股；原生模型金额为百万元人民币、股数为百万股、股价及每股价值为元，比例用小数。只有展示时换算亿元、百分比。
- 非年末经营流量采用 `TTM = 最近完整全年 + 本期累计 − 上年同期累计`，三项完整即可，不伪造单季度或把半年乘二。资产、现金、债务和股本用实际余额期，不作TTM加总。
- 历史增长用全年对全年或同窗TTM对同窗TTM；TTM除以上个年报不是同比。未来第1年可为基期结束后12个月，必须标明日期区间，不误写成完整日历年。
- 历史按现有可比年份读取，不强制十年或为此扩采早年；研发队列依所选摊销政策检查。零、缺项和不适用分开；D&A、非现金营运资本或历史FCFF未闭合就留空，预测总净再投资入口不因此自动受阻。

## 2. 保存基线并明确未来条件

保留原生来源初值的实际输入和结果作对照，不将它们认证为公司预测。用户已有完整条件优先；没有未来条件时交付明确命名的“数据库来源初值条件”，说明继承项和不足，不凭 Skill 内的教学数字生成最可能情景。

| 驱动项 | 必须解释的内容 |
|---|---|
| 收入／增长 | 增长来自价格、销量、份额或新业务的哪项主张；年度绝对收入、窗口与反例；历史同比/CAGR只是锚点 |
| 利润率 | 报表EBIT与研发／租赁调整后经济EBIT分开；完整年度路径优先于标量目标；不把历史均值自动认证为长期利润率 |
| 研发与现金税 | 费用、经济摊销、税务扣除和税基分别供给；沿既有 `research_tax` 契约，普通扣除不代表加计扣除资格 |
| 总净再投资 | 现有资本倍率及投入滞后，或完整 `annual_net_reinvestment` 预算；二者互斥，总投入已含研发，不再加扣研发或营运资本 |
| 风险／融资 | 参考与公司信用／经营暴露依据分开；人民币口径、债务成本及资本权重一致；借用行业参考须标代理 |
| 终值 | 稳态增长、税率、WACC和ROIC相配套；增长对应再投资，末期利润／研发／税盾衔接单独说明 |

每项重要条件记录“实际字段 → 取值 → 公司依据／用户输入／政策代理 → 原始方法出处 → 限制与可推翻证据”。不能按结果接近股价或价格高低选条件；多个故事并列，不擅自赋概率或宣称其中一个必然发生。

## 3. 复用已有计算与核验

按请求选择一条现有路径，不再写独立 DCF 计算器。具体输入格式读[统一入口](../../docs/guides/company-valuation-entry.md)，执行前核对当前 `--help`。

**已有显式参数，或使用来源初值进行完整条件交付：** 从仓库根目录、项目规定的隔离服务运行；变量须来自本次已核实配置，输出目录必须新建于 `workspace/derived/alphalake-dcf/`。

```bash
PYTHONPATH=valuation/backend .venv/bin/python -m tools.evaluate_native_policy \
  --database workspace/valuation.sqlite \
  --prepare-inputs --ticker "$ALPHALAKE_TICKER" \
  --output "$ALPHALAKE_DCF_RUN"
```

已有用户覆盖时加 `--overrides "$ALPHALAKE_OVERRIDES"`；已有完整请求时改用 `--explicit-input "$ALPHALAKE_REQUEST"`，不与准备模式混用。此工具会保留基线并交付已有增长／资本对照，不能把附加条件当成独立认证的预测。用户要求当前研发费用率／普通扣除对照时才加 `--research-tax`；原完整研发路径不替换。已确认网页地址时可加 `--web "$ALPHALAKE_WEB_URL"` 做实际 API 核验。

先读取 `native-conditional-delivery-v1` 的 `summary_file` 和 `protocol.json`，再按条件读取 request/result/review。工具已逐条件重放；补充核验对实际result调用 `tools.review_native_policy.review_report` 和 `tools.csi300_valuation.check_cashflows`，核对保存请求与返回输入、汇总值和来源哈希。`review_native_policy` 的CLI目录入口适用于另一种 `native-policy` 归档，不能混用。`selected_scenario=null` 正常，退出0不代表全部条件成功。run ID从程序摘要取得，不自行编造或传给不匹配的历史查询器。

**已有论点或故事需要完整年度输入：** 读[叙事契约](../../docs/valuation/narrative-valuation-plan.md)，沿 `tools.narrative_valuation prepare → run → verify`，同版范围收据不可省略。主张绑定 `revenue/margin/tax/capital/research/financing/terminal`，仅明确 `conditional` 的完整年度条件计算；故事不能覆盖财务、现金桥、股本或索偿。修订写 `previous_run_id`、递增故事版本，在新目录运行后 `compare`；核验与比较收据放不可变运行目录之外。

**只要单次 DCF、不需要额外条件组合：** 复用原生 `from-database`／`PATCH`／完整 `POST /api/valuation`；保存实际 inputs、完整响应、来源与实现版本，用上述 `review_report/check_cashflows` 核验。API会话 ID 仅用于读取和导出，不冒充持久化run ID。导出后删除研究临时会话，不能删除用户正在使用的网页会话。

### 方法与边界检查

- FCFF直接读引擎：经济EBIT减实际现金经营税，再减总净再投资。研发税基／摊销、NOL及融资税盾沿共享契约，不改成一条 `EBIT×(1−税率)` 覆盖复杂状态。
- 人民币WACC读实际分量和所选分支；总ERP若已含国家风险，不再叠加。现行总债务与市场权益配套加杠杆及权重，现金留股权桥，不借原skill的净债务权重替换。初始、逐年和终值WACC分别报告。
- 终值读引擎的正常化NOPAT、`g/ROIC`再投资及研发／NOL过渡现值；不得用末年FCFF简单增长替代。`WACC≤g`、非正ROIC或其他非法输入保留拒绝；终值占比高是待解释的敏感性，不设“必须50%–70%”门槛。
- 折现时序采用当前实际 `discount_factors`，不擅自改年中折现。现金、投资、债务、少数权益、优先权益、转债与员工期权按已选股权桥读取；不得既扣转股权利又加全转股股数重复摊薄。
- 独立现金汇总器 `check_cashflows` 按未叠加失败覆盖的现有聚合核对；采用失败／困境覆盖时，须核对覆盖前PV与覆盖后经营价值的实际关系，不能把正常覆盖差额误判为财务或DCF错误。
- 未闭合现金／投资口径、A股市值代理、行业资本倍率及税率代理分别提示。高增长、亏损、真实周期和负再投资不删样本；周期沿已批准框架，有限寿命、金融或其他不适用模型保留边界，不自行换模型。

## 4. 敏感性分析与 Excel

详细DCF交付至少包含一组与本次核心不确定性有关的敏感性对照；用户仅查询已有数值时无需重跑矩阵。优先展示已计算的条件。需要网格时使用明确标记的算术扰动，保留完整请求，以同一原生 API 重算每格；不得线性缩放每股值或用另写的简化Gordon公式代替。未经来源支持的扰动不是公司情景或概率区间。

标准网格可为5×5，轴有序且中心精确等于所选原条件，中心结果须复现原DCF。优先围绕实际生效的增长、利润率、资本效率或融资条件选轴；完整年度路径下不能只改一个已被覆盖的标量。WACC／g对照必须说明改变的是初始、长期还是完整融资路径，并保持其组件和终值ROIC政策一致；不能称所有关联变化都是单因素。非法组合标拒绝和原因，不补0；报告实际计算数。

Excel生成、来源批注、核验及公式限制见[Excel交付说明](references/excel-delivery.md)。复用现有完整工作簿导出，无需缺失的 `xlsx` skill、`recalc.py` 或新装LibreOffice。可在**新交付副本**添加中文摘要、来源／条件和敏感性页，不修改引擎或冻结运行归档。

## 5. 交付内容

所有动态产物写在 `workspace/derived/alphalake-dcf/<公司>/<本次标识>/`；既有故事／论点可留原目录并通过引用关联，不复制整个财务库。保存完整输入／响应、条件依据、源与实现版本、核验／敏感性收据和交付文件哈希。程序归档保持不可变，报告及Excel另放交付目录。

中文摘要应能直接回答：

1. 财务／TTM窗口、信息截止、报价日、股本基期、币种和数据新鲜度是什么。
2. 本次条件相对原生基线改了哪些输入，哪些只是沿用来源初值、行业参考或用户判断。
3. 各年收入、经济EBIT、现金税、总净再投资、FCFF、折现与终值如何形成经营价值，再如何桥接到每股值。
4. 核心敏感项、资本释放／终值衔接、来源缺项及可推翻主张有哪些；每股值是条件结果，不是获认证的目标价。
5. 原生/API重放、独立现金汇总和经济证据分别通过到哪一步；“算得出来”与“预测可靠”分开。报价对比注明 `条件值/收盘价−1`，旧报价不冒充实时价格。

结束前核对源快照未变、拒绝和缺项保留、文件可打开、所有引用存在、临时会话已释放。给出结果、关键条件、可点击交付文件及可追溯运行引用；不得声称未做的Excel公式重算、完整原文审核或预测有效性验证已通过。

## 来源与改写说明

基于 Anthropic [`financial-services` 的 `dcf-model`](https://github.com/anthropics/financial-services/tree/574ed3624aebd0418c7e96cd101262f30210ab26/plugins/vertical-plugins/financial-analysis/skills/dcf-model)，来源提交 `574ed3624aebd0418c7e96cd101262f30210ab26`，采用 [Apache-2.0](LICENSE.txt)。本版已改写为中文：统一AlphaLake取数、Linux/Python、人民币契约及共享引擎；移除外部公司取数、Office JS、逐阶段强制确认和美股示例默认参数。原skill单独保留作来源，不作为本版运行依赖。

原skill要求独立可编辑Excel公式模型；本版按项目现行冻结契约交付原生结果快照和API重算敏感性，明确保留能力差别，不维护第二套估值算法。所有方法以项目已核对的达摩达兰原始依据为准，不将上游skill中的公式、比率或版式当成理论规定。
