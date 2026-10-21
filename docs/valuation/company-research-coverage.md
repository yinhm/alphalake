# 完整范围的公司研究与条件估值

2026-10-21用户要求重新进行全部沪深公司的故事研究。原生数据与计算范围沿现有发布，金融仍属范围外；已有原始资料及研究保留，不能据批次日期自动退回默认。执行入口为`tools.coverage_research`，报告、模型和研究计划放workspace；不改变估值引擎、财务事实、参考选择或网页默认。

## 研究完成的含义

公司资料、原生基线、公司研究条件分别计数。公司研究至少明确：

- 产品、客户、竞争和赚钱机制；最近经营披露与同窗财务观察及相反解释。
- 增长、利润率、资本投入三项核心主张的公司证据，以及具体数值／持续期为什么这样选择。
- 完整年度收入金额、调整利润率、税率与资本条件；研发费用／扣除、融资和终值如何与其相配。
- 已采用的研究判断／参考代理、未解决问题、最强反证及下一次修订触发。
- 计算前冻结的实际输入、原生API结果、现金流审阅与可追溯来源。

完整结构、算术核验、经济依据和未来预测有效性分开。公司条件可以含有明确理由采用的代理和不确定性；未来数值不需要公告逐年承诺。但没有公司依据或尚未作出的重大选择必须标为待研究，不能用统一历史曲线、默认参数或行业问句补齐。仅阅读年报目录、引用哈希和得到估值数值都不证明完成公司研究。

旧217套显式条件中145套来自历史锚点、69套来自三年周期观察、3套是近期公司研究。全部保留并复核；路径存在不表示公司依据已经充分。收购／重述、许可或处置收入、利润与投资加回、有限经营期限等会改变预测或基期时单列处理，不在事实层猜扣金额或靠调预测遮盖。

## 研究输入

`coverage-research-plan-v2`包含完整范围、当批实际研究的既有`Story`及逐故事`basis`，不引入第二套DCF或参数生成器。`document.scope`须与当前SQLite的完整规范证券范围相同；`stories/evidence`只携带当批公司，不为其他公司复制基线或构造占位故事。原生叙事类型仍校验当批全部主张、证据及每家公司唯一基线。旧v1冻结研究按原提交复验，当前入口不保留旧版兼容。

`snapshot`绑定SQLite SHA-256、其来源库SHA-256、参考ID、信息截止和财务期。每个故事使用原有`Story`结构，包括完整`annual_operating_plan`与允许的`assumption_overrides`，沿现有typed契约核对期间、单位和长度。

| 内容 | 契约 |
| --- | --- |
| `basis[story_id]` | 七项：`revenue/margin/capital/research/tax/financing/terminal` |
| 每项依据 | `claim_ids`、公司具体`rationale`、`decision`及明确的`limitations/unresolved` |
| `decision` | `company_judgment`、`reference_proxy`或尚未决定的`unresolved` |
| 公司核心证据 | 增长／利润／资本绑定实际公司资料；仅snapshot或行业参考不足以成为公司研究依据 |
| 基线引用 | 当前公司`valuation.json`的`$.response`，核对消费快照；旧narrative基线可显式定位`$.body`，执行前仍须与当前数据库完整核对 |
| 来源 | workspace规范相对路径、SHA-256及具体定位；多个主张共用源文件，不复制PDF或完整基线 |
| 当批待研究公司 | `draft`及空年度路径合法，保持`pending`；范围外与财务缺项不填默认预测 |
| 非本批公司 | 完整分母中标为`not_in_batch`，不读取其基线、不重复计算；不据此撤销累计已完成的公司研究 |

研究者必须先审阅并写下条件。每项引用的全部主张必须直接绑定该主题，不能借另一主题的公司资料通过依据核对。工具只验证绑定与机械转换，不判断主张是否真实，不因为`company_condition`状态宣称经济认证或预测可靠性已经验证。`decision=unresolved`或缺少重要依据保留pending；明确采用的代理和普通未来未知不自动阻断。

## 执行

在项目根目录，按[隔离规则](../guides/fundamental-memory-20260919.md)串行运行。`ALPHALAKE_RESEARCH_WORK`指向workspace/derived内本轮工作目录，研究计划在计算前确定：

```bash
PYTHONPATH=valuation/backend "$ALPHALAKE_PYTHON" -m tools.coverage_research freeze \
  "$ALPHALAKE_RESEARCH_PLAN" --output "$ALPHALAKE_RESEARCH_WORK/plan.frozen.json"

PYTHONPATH=valuation/backend "$ALPHALAKE_PYTHON" -m tools.coverage_research check \
  "$ALPHALAKE_RESEARCH_WORK/plan.frozen.json"

PYTHONPATH=valuation/backend "$ALPHALAKE_PYTHON" -m tools.coverage_research run \
  "$ALPHALAKE_RESEARCH_WORK/plan.frozen.json" --output "$ALPHALAKE_RESEARCH_WORK/run-v1"
```

先用`run --ticker …`执行小批，`--web`仅在实际网页目标明确时交叉核验并释放临时会话。新运行目录不能覆盖，计划或依据变化必须另建冻结版本。冻结同时保存逐故事规范实际请求的SHA-256，发送API前再次核对；转换实现演进也须重新冻结，不能只凭计划文件未变沿用旧收据。冻结后修改计划、来源变化、串公司或串参考、非法事实覆盖及不完整年度输入均拒绝。

每个可执行故事先与当前原生数据库基线对账，再通过已有`compile_story`、`calculate_request`和`check_operating_plan`。输出当批各公司请求／响应／审阅／绑定与完整范围审阅计数；pending不执行默认估值，拒绝不当作0。默认只执行本批故事；`--ticker`只能选择其中公司。小批结果必须标明实际选择集合，不能冒称全范围已经计算。累计交付引用相同财务／参考快照及方法版本的既有已核验研究，新批次只增加或显式修订本公司；不能重算全部旧公司或把非本批状态误作退回待研究。

研发预算按人工年度条件机械展开，普通扣除不表示已审核税法优惠；旧路径重建时核对终值费用是否仍表达同一费用率、年度窗口与新终值增长。资本倍率与年度总净预算遵守既有互斥契约；亏损恢复、短历史和范围缺口不借研究入口绕过引擎边界。

倍率路径须核对实际投入时点：沿原生默认领先1年时，第1年投入为第2年增收融资，不能将第1年增收除以倍率称为实际第1年支出。读取审阅的`capital_funding.revenue_year/incremental_revenue_million_cny/recomputed_reinvestment_million_cny`说明金额及融资压力；显式总净预算按投资年消费，不应用这一领先期。改变时点需研究者明确选择并重新冻结，不能为对齐文字偷偷改变参数。

终值的关系判断不能被旧基线常数替代。研究者选择无新增超额回报时，既有`roic_stable_override=null`随本条件的实际终值WACC及税盾执行；照抄旧亏损基线WACC会意外保留超额回报。显式未来税率与普通扣除须准确标注其研究条件，不能称已报告有效税率。报告列明末年及终值投入、同一终值年继续原资本规则的差额与FCFF影响；方向可能增加也可能减少，不称平稳成熟或已经出售资产。前段负FCFF须解释融资成立条件，不因账面低债而认证资金充裕，也不从价值重复扣除已消费的投资。

报告按[研究交付](../guides/research-delivery.md)登记；`/screen`以明确的公司研究批次交付，基线、诊断与研究条件分开，保留全范围待补状态。不先把原生基线设成新的研究结论，也不在算后按价值或股价挑故事。

## 连续批次

`tools.coverage_research_notes`读取已完成的分析师索引及登记的全范围原生基线，机械装配本批计划。索引每项包含证券、结构化笔记与同名正文的workspace相对路径、结构化笔记哈希；两份笔记与采用索引一同冻结。笔记使用`alphalake-analyst-research-note-v1`，需写明消费及参考版本、实际公司来源、七项依据和具体年度数值；工具不推断或选择缺少的参数。缺公司参考允许登记pending诊断，不能产生可执行条件。原生基线不重新标成公司事实；研究复核明确标为`research_judgment`，不冒充事实。研发普通扣除沿已批准条件展开，终值费用采用本笔记终值增长，不能误用旧基线增长。

采用索引的`purpose`只允许`company_condition`、`diagnostic_condition`、`deferred`。公司条件仍要求笔记明确采用；诊断须已有完整年度数值并显式选择执行，报告及选股行注明“仅作诊断，未采纳为公司主值”；延期不计算。不能把可算诊断说成缺财务，也不能把诊断计入主条件数。索引用途与原笔记共同冻结，补算不改写计算前原稿；旧标签须显式转换并保留原索引及原因。

本批完成`freeze/check/run`后，`tools.coverage_research_delivery`接收运行目录、前一选股批次ID、原生基线报告ID及新的交付ID，生成本批公司报告、图表和累计总览。它只复用相同财务、参考、计算及审阅版本的旧公司结果；新公司逐家公司新增，重复覆盖必须另行明确修订，不能暗作新增完成数。累计冻结时间各公司分别保存，不伪造一个全市场同时冻结的日期。

修订索引须用`previous_report_id`绑定当前该公司报告，交付另建ID及前版关系；累计唯一公司数不因补算／修订增加。无此关联的重复公司拒绝。累计分别统计实际研究、公司条件、诊断条件和仍无新值的公司，不用全部可计算数代替公司研究采用数。

累计快照完整保留原分母，验证登记及模型哈希后原子切换`/screen`。新报告只引用旧研究，不复制全部旧模型／PDF或重新执行旧条件。每批均保留本批公司数、累计研究数、条件计算数及未解决数，固定队列未处理完不能称全市场研究完成。研究计划、结果、图片和所有运行收据均留workspace。

资本复核可在索引显式附`capital_review`及哈希，工具把其中标准来源一起冻结；报告展开该公司数量级判断，不把三项现金流调节称为完整营运资本。明确期后资本尚未桥接时，笔记用`price_comparison.status=unreconciled_post_period_capital_change`及具体原因收紧既有比较边界，不能反向放开；重述相同原生状态沿用原来源限制，不替换原因。登记失败保留当前选股指针；重试只复用来源／运行完全相同的已登记公司报告，不覆盖旧签名。未登记非空目录须核对失败产物后恢复。

资本复核文件统一使用`source={path,sha256,locator}`与`companies`数组，各行至少包含`ticker`和`assessment`正文，其他数值／判断字段可保留。它可以只审阅本批部分公司；没有该行不冒称经过数量级复核。其他草稿格式须显式转换为新文件和新采用索引，保留原路径／哈希，不添加运行时多格式降级或修改已冻结来源。

连续操作仍须按上文独立服务、串行执行；以下在同一已确认工作目录内使用：

```bash
PYTHONPATH=valuation/backend "$ALPHALAKE_PYTHON" -m tools.coverage_research_notes \
  "$ALPHALAKE_ANALYST_INDEX" --baseline-report "$ALPHALAKE_BASELINE_REPORT" \
  --output "$ALPHALAKE_RESEARCH_PLAN"

# 随后执行上文freeze/check/run，保持本批输入和目录不可变。
PYTHONPATH=valuation/backend "$ALPHALAKE_PYTHON" -m tools.coverage_research_delivery \
  "$ALPHALAKE_RESEARCH_RUN" --previous-dataset "$ALPHALAKE_PREVIOUS_DATASET" \
  --baseline-report "$ALPHALAKE_BASELINE_REPORT" --id "$ALPHALAKE_RESEARCH_ID"
```
