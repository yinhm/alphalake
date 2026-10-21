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

`coverage-research-plan-v1`直接包含既有`narrative_valuation.Document`和逐故事`basis`，不引入第二套DCF或参数生成器。`Document.scope`须与当前SQLite的完整规范证券范围相同；小批执行可显式选择ticker，完整分母仍保留。

`snapshot`绑定SQLite SHA-256、其来源库SHA-256、参考ID、信息截止和财务期。每个故事使用原有`Story`结构，包括完整`annual_operating_plan`与允许的`assumption_overrides`，沿现有typed契约核对期间、单位和长度。

| 内容 | 契约 |
| --- | --- |
| `basis[story_id]` | 七项：`revenue/margin/capital/research/tax/financing/terminal` |
| 每项依据 | `claim_ids`、公司具体`rationale`、`decision`及明确的`limitations/unresolved` |
| `decision` | `company_judgment`、`reference_proxy`或尚未决定的`unresolved` |
| 公司核心证据 | 增长／利润／资本绑定实际公司资料；仅snapshot或行业参考不足以成为公司研究依据 |
| 基线引用 | 当前公司`valuation.json`的`$.response`，核对消费快照；旧narrative基线可显式定位`$.body`，执行前仍须与当前数据库完整核对 |
| 来源 | workspace规范相对路径、SHA-256及具体定位；多个主张共用源文件，不复制PDF或完整基线 |
| 待研究公司 | `draft`及空年度路径合法，保持`pending`；范围外与财务缺项不填默认预测 |

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

每个可执行故事先与当前原生数据库基线对账，再通过已有`compile_story`、`calculate_request`和`check_operating_plan`。输出各公司请求／响应／审阅／绑定与完整范围摘要；pending不执行默认估值，拒绝不当作0。小批结果必须标明实际选择集合，不能冒称全范围已经计算。

研发预算按人工年度条件机械展开，普通扣除不表示已审核税法优惠；旧路径重建时核对终值费用是否仍表达同一费用率、年度窗口与新终值增长。资本倍率与年度总净预算遵守既有互斥契约；亏损恢复、短历史和范围缺口不借研究入口绕过引擎边界。

报告按[研究交付](../guides/research-delivery.md)登记；`/screen`以明确的公司研究批次交付，基线、诊断与研究条件分开，保留全范围待补状态。不先把原生基线设成新的研究结论，也不在算后按价值或股价挑故事。
