---
name: alphalake-valuation
description: 使用AlphaLake数据与既有引擎完成沪深非金融公司的中文基本面研究与条件估值，将业务、竞争、财务质量和资本配置证据接到年度假设，以图表解释每股价值、关键分歧及反证，在research页面交付。用于初次覆盖、估值解释、财报或预告更新及版本比较；独立建模校验使用alphalake-dcf，数据刷新发布使用alphalake。
---

# AlphaLake 公司研究与估值

完整交付应让一个此前不了解公司的读者，读懂**卖什么、卖给谁、如何赚钱、优势与代价、财务是否健康、未来需要兑现什么，以及每股值多少**。不能只交估值接口说明、同比复述或几张论点卡。按用户问题选择工作深度；只问已有结果时不重做整篇研究。

本Skill由AlphaLake改写，研究流程借鉴Anthropic financial-services；来源、版本与取舍见[来源记录](UPSTREAM.md)，许可证见[LICENSE.txt](LICENSE.txt)。不要求另装这些上游Skill或订阅外部终端。

## 选择工作方式

| 用户意图 | 工作范围 | 按需读取 |
| --- | --- | --- |
| 首次研究／完整估值 | 连续完成七步；交付独立可读的公司研究、明确每股条件值、重要图表及计算依据 | [研究与论点](references/research-and-thesis.md)、[假设论证](references/assumptions.md)、[交付与解释](references/delivery-and-explanation.md)、[图表](references/charts.md) |
| 解释一个已有结果 | 找到保存的运行，核对原时点及实际输入，回答价值驱动 | [交付与解释](references/delivery-and-explanation.md)；无ID再读[更新与比较](references/updates-and-comparison.md) |
| 深挖增长／利润率／WACC等单项 | 查该项输入、依据、相关约束和反例；仅获授权时计算条件对照 | [假设论证](references/assumptions.md) |
| 新财报／预告／宏观经营证据更新 | 逐项比较事实、论点与条件，保存修订；宏观先核对真实变化与公司暴露，没改变输入则解释即可 | [更新与比较](references/updates-and-comparison.md)、[宏观研究链](references/research-and-thesis.md#宏观证据按经营暴露选取) |
| 两次估值为何不同 | 先比真实输入，再区分事实、预测、折现与股权桥；不凭标签猜原因 | [更新与比较](references/updates-and-comparison.md) |

完整任务已获授权时连续完成，不逐阶段重复确认。缺必要身份、文件或用户选择时，只问真正阻塞的问题；同时推进其他独立部分。没有经济依据时保留条件或拒绝，不以询问代替研究，也不伪造确定性。

研究深度取决于问题，而非可调用工具的数量。先定位已有报告／论点和冻结计算，明确本轮是**解释旧条件、提出新研究条件，还是更新已有条件**；把最影响判断的经营分歧写成研究问题。已知数据足以回答就交付，不默认再跑全市场、独立DCF或完整敏感性矩阵。

## 开始前核对

1. 定位仓库：会话已知路径或`ALPHALAKE_ROOT`优先，否则检查当前目录及父目录的`valuation/backend/tools/company_valuation.py`。安装Skill目录不等于仓库目录。读取根`AGENTS.md`、[当前状态](../../docs/implementation-status.md)与[统一入口](../../docs/guides/company-valuation-entry.md)；涉及取数或转换再查[数据契约](../../docs/valuation/data-contract.md)。
2. 默认主库`workspace/alphalake.duckdb`，原生消费`workspace/valuation.sqlite`；从现行发布收据及[数据契约](../../docs/valuation/data-contract.md)确认版本、范围、哈希、报告期、信息截止、收盘日、股本基期与参考版本。分别判断数据能否消费、证据是否够解释假设；全市场的部分缺项不阻断其他公司的研究。未知留空，不能把旧运行说成今日最新。SQLite不是第二事实／审核库，也不是严格历史PIT认证。
3. 复用已确认的Python环境、数据与用户覆盖。原生准备只需合格SQLite与证券，**不普遍要求BatchPolicy**；只有专项／行业入口要求相应政策。准备输入中的`user_explicit`只是基线名，不代表全部由用户提供。
4. 正式报告、论点、模型及最终结果写入`workspace/research/<id>`，不提交项目仓库；按[研究交付契约](../../docs/guides/research-delivery.md)最后登记`research.json`，校验后交付`/research/<id>`链接。临时计算／内部验收留derived，已有冻结运行显式引用；引用规范源文件，不复制PDF。真实批量重放依项目资源规则串行隔离，不随估值请求升级主库、刷新全市场、发布或安装调度。

## 完整研究的七步

1. **冻结起点**：证券身份、数据与方法版本、财务／信息／价格／股本时点；记录准入与缺项，并核对最新已取得的中报／预告是否只进了正文还是已进入计算。按[数据与证据核对](references/research-and-thesis.md#先核对数据与证据)区分有效来源、过时结果与真正缺项。已有结果可作为基线，不能把继承初值当已认可预测。
2. **理解公司与财务**：阅读已有年报、最近中报及重要更新，拆解产品／客户／渠道、价值链、行业与竞争、财务质量、管理层资本配置。主要判断写清“数据观察→经济机制→替代解释→估值后果”，不以笼统的品牌／技术优势代替证据。见[研究与论点](references/research-and-thesis.md)。
3. **建立可证伪论点**：围绕增长、利润率、再投资写主张、支持证据、最强反例和触发条件；指出证据能区分什么、尚不能区分什么。缺证据单列，不将管理层计划等同实现。
4. **形成成套假设**：先记录研究条件及采用依据，再计算；用[证据到参数的对应表](references/assumptions.md#从主张落到实际年度输入)写明为什么是这条路径及持续期、替代路径为何不同。联动收入、利润、研发／税务、资本、融资及终值。已看过旧值如实说明，不声称盲选；不得把规则候选称为合理参数自动选择器。
5. **计算并核验**：调用下表既有入口，保存完整请求／响应、运行ID与证据。按[执行与读取](references/delivery-and-explanation.md#执行与读取)核对真实输出位置、状态、拒绝及经济审阅；不在报告或Skill中重写估值公式。数值异常先追踪输入，不因结果难看反向改故事。
6. **图表化交付明确估值**：首页给主研究条件的每股点值、其他条件及成立依据；从收入到FCFF、终值和股权桥说明价值。完整报告通常选4—8幅能回答重要问题的图，正文嵌入、逐图解释并登记来源；数据不足用表或说明原因，不造图凑数。复用`tools.research_charts`绘图，不重算模型。使用[报告模板](assets/report-template.md)及[交付规范](references/delivery-and-explanation.md)、[图表契约](references/charts.md)。
7. **保存与修订**：记录主张、条件、输出及下一次观察事件；已有工具按其契约生成ID与修订关系。新证据未改变条件时也可只记录论点变化，不强行重算。见[更新与比较](references/updates-and-comparison.md)。

## 复用现有入口

在`$ALPHALAKE_ROOT/valuation/backend`用已核实的`$ALPHALAKE_PYTHON -m tools.…`调用；参数与产物以链接spec及当前`--help`为准，不照抄历史样例。不要为本Skill另写计算器、假设选择器或兼容入口。

| 任务 | 入口与权威说明 |
| --- | --- |
| 从SQLite准备原生条件、完整显式请求交付 | `tools.evaluate_native_policy --prepare-inputs --ticker …`，可加已确认`--overrides`；已有请求用互斥的`--explicit-input`。[具名交付](../../docs/guides/company-valuation-entry.md#原生具名条件自动交付) |
| 主张／证据到年度故事 | `tools.narrative_valuation prepare/run/verify/compare`；prepare需同版`alphalake-native-coverage-v1`收据。[叙事契约](../../docs/valuation/narrative-valuation-plan.md) |
| 既有专项／行业政策 | `tools.company_valuation`，消费BatchPolicy；检查`alphalake-company-valuation-v1`及selection，不从候选自行选价。[统一入口](../../docs/guides/company-valuation-entry.md#使用) |
| 标准经营依据与经济审阅 | `tools.review_operating_evidence`、`tools.review_native_policy`；来源金额元与估值百万元分开。[经营事实](../../docs/guides/company-valuation-entry.md#从标准库取得经营事实依据) |
| 研究图表 | `tools.research_charts --input … --output …`，仅绘制已核对数据和现有引擎输出，写新研究目录；不改事实、参数或页面。[图表输入与交付](references/charts.md) |
| 完整范围初次覆盖 | `tools.initial_coverage freeze/collect/facts/calculate/build/catalogue`；冻结实际发布范围及当前原生条件，保留逐公司缺项／范围外，复用健康经营缓存并流式取事实。[批量执行与验收](../../docs/guides/research-delivery.md#完整发布范围的初次覆盖) |
| 经营证据与预测约束 | `tools.review_operating_constraints`；保留公司分母，核对单位／期间、分部增长贡献及显式条件；区分历史归因、条件关系和未建立，半年结构不套入TTM。[约束契约](../../docs/valuation/narrative-valuation-plan.md#经营证据到预测约束) |
| 运行发现／旧政策运行比较 | `tools.list_valuation_runs`、`tools.compare_valuations`；故事与具名交付ID另有命名空间。[更新与比较](references/updates-and-comparison.md) |
| 已有跨公司／周期／资本效率条件 | 只在问题涉及时读取[统一情景](../../docs/valuation/native-uniform-scenarios.md)、[周期框架](../../docs/valuation/cyclical-valuation.md)、[资本效率](../../docs/valuation/capital-efficiency.md)，复用现有工具，保留全部拒绝与条件 |

原生具名交付读`native-conditional-delivery-v2`的`summary_file`；故事读`narrative-valuation-v2`摘要及其报告，再展开实际请求／响应。不将计算成功、`ready`或重放一致当经济认证；`selected_scenario=null`不需要选胜出者。专项入口退出0成功、2业务阻断、1运行失败；其余入口按各自契约读状态，非零仍读JSON和stderr。专项受阻不能偷偷换行业候选，失败不等于估值0。

## 交付前用读者视角检查

- 不依赖旧报告也能说清公司的生意、规模、主要业务贡献、竞争和现金约束；重要数字有期间与来源。管理层讲决策及结果，不堆履历；行业讲公司真正能服务的需求，不把大市场等同收入机会。
- 基本面分析至少解释收入质量、利润可持续性、利润与经营现金差异、研发／资本投入及融资能力。没有证据的因果关系写成待检验解释，不能用“风险存在”结束。
- 能明确找到每股点值、币种、资本基期、实际条件与输入，以及读者最需要判断的分歧。主条件是研究判断，不是自动选择器、最可能情景或全项目新默认。证据不足仍给已有可计算条件，说明改变哪项证据会改变路径，不隐去数字或包装成当前目标价。
- 图与正文、表格、已保存结果一致，事实／预测分开；每图有一句洞见及来源，历史不足不补零，未计算敏感性不画矩阵。登记后实际打开`/research/<id>`，核对图片、附件与窄屏阅读。

## 不能跨越的边界

- 默认沪深非金融；金融标范围外，多市场及历史不足估计仍延期。现成条件能计算时缺历史单列，不泛称公司无法估值，不扩采早年凑队列。
- 财务／参考、政策代理、研究判断与用户覆盖分层记录；来源初值不是公司预测。用户完整输入优先，显式0不当缺项。事实通过现有数据边界获取，不在取数层按公司覆盖EBIT。
- 标准累计三项可形成TTM，季度缺项不等于TTM缺项；prepared的`quarters_since_10k=0`是关闭重复轮转。公告日期或CNINFO关联缺失不阻断有效当前财务；未知日期不伪造。
- 方法核对达摩达兰原始资料与项目方法契约；Anthropic只提供研究组织借鉴。引擎、默认及新输入接口仍冻结；改进提案记入[valuation TODO](../../valuation/TODO.md)，不从假设生成器绕过。条件研究不自动改网页默认或发布。
- 业绩预告仍为独立数据，不写财报／TTM、不取中点当事实、不把净利润等同EBIT。自动预告→故事绑定未实现，不能新增不存在的证据类型；当前可独立引用、解释对论点的影响，数值条件须按现有契约显式给定。
- 经济信息不足时指出缺什么、影响哪项决策，并交付已有条件及限制；不要无止境追PDF、把详细未来资本预算设为所有公司的新门槛，或用贴近股价证明假设正确。
- 日常同步用[alphalake](../alphalake/SKILL.md)；独立、只复用数据的DCF校验用[alphalake-dcf](../alphalake-dcf/SKILL.md)。普通完整研究不强制运行独立模型，也不把原生重放称独立验证。只有Excel为交付目标时再读取可用的xlsx Skill。
