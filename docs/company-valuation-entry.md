# 统一公司估值入口

## 已保存预测的到期核验

新增只读命令`tools.review_valuation_forecast`，使用已有run ID核验历史增长及显式一年校准政策的十年收入/调整EBIT预测。先复用`load_run`检查请求/引擎哈希，再用当前引擎完整重放原报告；当前引擎不能复现时拒绝，不悄悄改用新预测。此工具不执行新估值，不改政策或旧运行。

```bash
# 仓库根目录；实际使用时填写已有run ID和本次实际值信息截止。
PYTHONPATH=valuation/backend python -m tools.review_valuation_forecast \
  workspace/auto-valuation-20260909/market.duckdb RUN_ID \
  --as-of 2026-09-11T00:00:00+08:00 \
  --run-directory valuation/backend/data/alphalake_runs > forecast-review.json
```

目标期按原财务期逐年递增并保持季度末，例如2026H1的第一年目标为2027H1，不是2026年报。仅对已经结束的目标期调用现有`export-valuation`，所有实际值固定到用户提供的`--as-of`。报告期虽结束但尚无公告/标准数据时仍阻断，不借未来数据补齐；未到期位置始终保留且不查库。专项、显式逐年手工模型暂不套用这一比较口径。

输出为`alphalake-forecast-review-v1`，包括源run ID、原/当前引擎版本、核验器代码哈希、两种信息时点、10个位置状态及内容寻址`review_id`。到期位置保存本次实际导出与哈希、所消费事实ID，以及预测、实际、有符号/绝对误差（百万元）；实际收入为正时另算绝对误差/实际收入百分比。收入与调整EBIT分别报告缺项：EBIT缺字段不会抹掉已可核验的收入。亏损实际值保留；非正收入只将百分比留空，不删除该位置。

实际值复用`standard_window_reader`核对已消费源位、期间、单位和血缘。身份不同、来源冲突、已知金融业务、错误窗口或源位不一致会阻断；不能只看进程退出码判定全部可评价，应检查`statuses`及逐指标状态。沿用原非金融经营范围假设不等于重新完成业务可比性审核，其他经营范围变化仍可能需要人工复核。

运行首次创建时间尚无可验证的独立记录，不能仅凭run ID/财务信息截止声称当时已保存预测；该入口不自动证明严格PIT或独立留出。实际导出随每次评价保存在输出中，更正后产生新评价内容；请保留旧评价文件以固定实际值版本。这里只核验收入和原政策调整EBIT，`actual_fcff`仍为空，不评价股价、FCFF、终值或每股估值准确度，也不自动选择误差较小的政策。

[两份真实运行验收](acceptance/forecast-review-20260911.json)：安克/苏泊尔2026H1请求均完整重放，分别保留10个未到期位置；将导出可执行文件设为`/bin/false`仍通过，证明未提前查询。该次主运行目录核查的3,117条记录均为2026H1，没有一年到期结果；这不是3,117家公司计数。随后[安克2025H1标准历史验收](../valuation/research/standard-history-2025H1/README.md)已在真实副本补齐历史输入，用原模板生成事后运行并完成2026H1实际核验：1项到期、9项未来。完整运行/实际快照已压缩归档并进入离线回归；不冒充2025年已留存预测或新独立准确度实证。

新增回归覆盖到期金额、幂等内容、局部缺项、已消费源位篡改、身份/期间/来源冲突、重复事实、亏损和零收入保留、未来零查询及逆时点拒绝，已通过；进入现有pytest CI。使用已有后端Python、Go真实样本依赖，无新依赖；本轮Go全套和构建通过，未重跑未受影响的全部后端研究测试。

## 历史增长政策的增长路径对照

成功的`nonfinancial-history-fcff-v1`及显式一年校准政策现在自动输出`valuation.growth_sensitivity`；API/完整归档中为顶层同名字段。专项、显式逐年预测及仅企业价值模型返回null，不代表这些模型没有增长风险。无需新CLI参数，也不另外查询数据库或取网络数据。

对照复用同一引擎，将前五年增长设为0，后五年从0线性过渡至原终值增长；利润率、税率、资本效率、WACC、终值增长/ROIC与股权股本调整输入保持原值。第一年校准的利润率同样保留，不能把两种调整混称单一校准效果。金额变化包含收入路径对利润、再投资和终值金额的全部影响；不是只改第一年或只改终值。前五年净收入增量再投资为零，不等于维持性现金资本开支为零。

结构化结果包含原/对照增长数组、十年FCFF数组（百万元）、原/对照每股条件值及差额（CNY/share）。`evidence`同时说明两批共同中期窗口平均改善、一年期未通过和部分中期窗口退化，并绑定[复核回执](acceptance/zero-growth-common-windows-20260911.json)哈希；零增长没有被采用为默认或公司预测。第四年至终值仍为未验证延伸，不是价格区间、概率界限或推荐值。非正对照股权残值明确标为需要困境模型，不覆盖原成功结果。

[两份真实归档请求验收](acceptance/growth-sensitivity-20260911.json)已完成：安克原126.1378元、对照55.1418元；苏泊尔原43.2526元、对照44.5217元。财务期均2026H1，信息截止沿用原请求；不是更新财务后的估值。两份完整主报告、请求及旧文件哈希不变，差异输入仅为逐年增长；WACC完整结果及桥接调整/股数相同，经营价值、股权价值与每股值随模型计算变化。新输出更新引擎版本/run ID，原运行保留；新运行位于`workspace/growth-sensitivity-20260911/runs/`。安克55.14元与此前42.28元示例并非同一套WACC等基线，不能串作价格更新。

真实验收未查询主库，复用已有标准链请求并通过当前引擎完整重放。现有集成回归覆盖独立现金流/终值复算、请求不变与输入不变、零增长同值、幂等保存、对照负股权残值、专项不套用及统一摘要透传，进入现有pytest CI；依赖现有后端Python及Go自包含样本，无新依赖。只增加一次引擎内存计算，不修改默认政策或财务源。 本轮后端全套385项通过、4项既有外部样本跳过、7条警告（487.77秒）；最终证据哈希及负残值补充回归通过。Go全套与构建通过；无Go代码、依赖或CI配置变更。

## 通用DCF的方法范围与缺口

成功的通用账面／历史／校准DCF在`valuation.method_assessment`返回结构化方法披露，完整API及保存运行对应顶层同名字段。状态为`conditional_fcff_not_full_company_valuation`：标准事实来源与血缘入口、经营EBIT调整、研发费用化／租赁范围、预测政策来源、资本效率参考／政策、历史FCFF缺项、资本释放年份、WACC来源与终值风险政策、末年及终值再投资率、终值占比、股权桥接边界和`unresolved`五类事项分别展示。

它不是新的评分、自动政策批准或估值修正。必需输入仍按原校验拒绝，未分类金融投资、期权等经济缺口在显式账面情景下保留；不把未定价当不存在。专项／仅企业价值返回null；旧归档没有该字段时摘要也返回null。共享M4在缺期初资本时将`implied_roic_projections`各年及`implied_roic_terminal`留空，不借未来投入构造期初资本；API列表允许null，前端类型同步。含旧ROIC诊断的历史报告不能通过当前引擎的整报告严格重放；应保留旧文件，以原请求生成新运行，再比较或核验，不放宽旧报告一致性检查。方法依据、两公司复验和限制见[本轮验收](../valuation/research/method-closure-20260912/README.md)。

## 通用DCF的终值回报对照

成功的通用账面DCF（含历史增长和显式一年校准政策）现在自动输出`valuation.terminal_sensitivity`；完整API/归档运行对应顶层同名字段。内容包含原终值ROIC、终值WACC、增长率、终值现值占比、原每股条件值、`counterfactual_value_per_share`与差额，金额单位为CNY/share。专项模型和仅企业价值模型输出null；不由此推断其没有终值风险。

对照复用现有引擎，只把终值ROIC设为终值WACC，保持显式期现金流、折现及股权桥接。它是达摩达兰[无持续超额回报](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/termvalueexreturns.htm)情景，不是公司回报估计或推荐价格；不覆盖原`value_per_share`、不自动切换政策。对照若出现非正股权残值，以`counterfactual_equity_status`标明需要困境模型，不改原成功结果。原ROIC已等于WACC时复用原报告，其余通用结果增加一次引擎内存计算，无额外数据库查询或网络请求。

已完成[两份真实标准请求验收](acceptance/terminal-sensitivity-output-20260911.json)：安克、苏泊尔原请求、解析输入和完整报告均与历史运行一致，对照分别119.8340元、38.0005元，匹配前轮单因素复算。新增输出使引擎版本/run ID更新，不是财务或估值变化；旧运行原样保留。此验收重放归档请求，没有重新查询主库。Python回归覆盖终值ROIC与WACC的高/等/低关系、零增长、幂等归档、专项不套用及统一摘要透传；依赖现有后端Python环境和自包含Go真实样本，进入现有pytest CI。 本轮Python全套375项通过、4项既有外部样本缺失跳过、7条既有警告（474.76秒）；补充非正对照分支回归通过，Go全套及构建通过。没有Go代码或依赖变更。


`tools.company_valuation` 从实际数据库按代码查询全部候选证券身份（`valuation-readiness --code`），不先计算全市场财务就绪度；共享一次当前标准估值输入（显式现金检查另读上年同期窗口），按显式提供的政策集合计算候选结果并输出JSON。复用`run_batch`、参考版本选择及共享估值引擎；不启动HTTP服务，不读取样本PDF直算，不自动发现或批准目录中的政策。

## 使用

在`valuation/backend`下，使用已安装后端依赖的Python。行业政策可按[全市场运行说明](a-share-automation-acceptance-20260910.md)展开；`--policy`接受现有`BatchPolicy`格式，可重复传入不同版本。

```bash
python -m tools.company_valuation /absolute/market.duckdb 300866 \
  --period 2026-06-30 --as-of 2026-09-10T04:42:23Z \
  --policy data/combined-policy.json \
  --reference-database /absolute/references.duckdb > company.json
```

将已有双公司专项政策装入单独配置，无需复制计算逻辑：

```python
import json
from pathlib import Path
bundle = dict(policy_version='reviewed-companies-2026H1-v1',
              review_note='已审核双公司政策，实际输入和时点仍须通过检查', assignments={})
for code, filename in [('300866', 'anker-2026H1-revised.json'),
                       ('600519', 'moutai-2026H1-central.json')]:
    bundle['assignments'][code] = dict(policy=json.loads(Path('../examples', filename).read_text()))
Path('data/reviewed-policy.json').write_text(json.dumps(bundle, ensure_ascii=False, indent=2))
```

然后同时传入`--policy data/combined-policy.json --policy data/reviewed-policy.json`。这是安克7.5%固定WACC的修正专项政策，不是173.75元所用的市场WACC/债务现值政策；后者须按既有`Assignment.wacc_binding`提供匹配的参考及市场资本输入，不能靠改政策名称替换。

默认选择规则：

1. 本地证券身份或源记录冲突先阻断。
2. 配置中有该代码的显式公司assignment或exclusion时，优先于行业配置；不等于程序自动认定该政策更准确。
3. 同级多个公司配置，或多个命中/待核验的行业候选，返回`blocked_ambiguous_policy`；不按价格高低或文件顺序选取。
4. 公司政策过期、缺补充输入或不适用时，保留其阻断；行业候选即使成功也不会悄悄成为默认结果。
5. 可用`--select reviewed-companies-2026H1-v1`显式选择传入的版本。被选政策仍执行全部生产校验，不会因手动选择绕过缺项和时点规则。

同一份BatchPolicy内部沿用现有公司assignment优先、公司排除优先及行业歧义拒绝规则。若要并列展示专项和行业结果，应提供独立配置。所有候选都会尝试计算，`--select`只决定顶层选中结果，不跳过候选的校验。

## 显式现金交叉检查

增加`--cash-check`后，入口为选中的普通账面/历史DCF并列返回`cash_check`，不修改估值、输入或run ID。当前仅支持H1起点；专项模型、其他季节或未选中估值分别返回明确状态。使用同一数据库、同一信息截止导出上年H1窗口，验证证券身份、重复期间一致性、源位模式、单位、季度/累计口径与完整血缘；不回退到研究快照或PDF。

```bash
python -m tools.company_valuation /absolute/market.duckdb 300866 \
  --period 2026-06-30 --as-of 2026-09-10T22:18:56.906406Z \
  --policy ../examples/nonfinancial-baseline-2026H1-pilot.json \
  --cash-check > company-with-cash-check.json
```

[原研究](../valuation/research/tdx-operating-cash-forecast/README.md)采用两期TTM现金流率均值×当前TTM收入，资本开支沿用当前值，曾在原120家三起点通过；[新120家复验](../valuation/research/tdx-operating-cash-replication/README.md)未通过，不能只据原6.84%改善宣称普遍有效。`cash_check.evidence.research_replication`返回失败状态、协议/摘要哈希、样本分母、误差和门槛；现金公式及选中估值不变，检查证据变化会改变check ID。检查是当前生成的回溯研究核对，不声称该规则在历史估值截止已经可用，也不把现金代理当成FCFF。

`cash_check.evidence.research_uncertainty`现返回版本化的条件重采样证据：分别列出全留出360组（331可评价）与既定预测输入子组255组（244可评价），保留120家公司抽样分母及子组外105组。两套总体OCF/现金代理主误差与WAPE统计均包含点估计、分层区间、不分层敏感性区间及明确单位，并绑定协议和结果哈希。方法为9,999次公司整组抽样、95%逐项百分位区间，三起点及实际值版本截止显式列明；原逐年结果见[全留出复核](../valuation/research/tdx-operating-cash-forecast/uncertainty.md)和[子组复核](../valuation/research/tdx-operating-cash-forecast/scope-uncertainty.md)。

这些是历史误差改善的不确定性，**不是当前公司现金流或估值上下界**。`current_company_applicability=not_established_by_research_summary`明确表示未凭这份摘要判定当前公司的适用性；子组输入齐备不等于完整DCF准入。单期区间跨零、现金代理与WAPE证据较弱、17个单公司行业层和共同冲击等限制随结果返回，不用摘要扩大准入或修改估值。证据以发布时固定内容随代码交付，正常估值无需读取研究目录或重新运行重采样；回归核对每个导出区间与其绑定原结果一致。

成功时列出现金预测、DCF第一年FCFF/再投资，以及两者差额，金额均为人民币元的十进制字符串（引擎原结果为百万元）。按`差额=(DCF NOPAT−预测OCF)−(DCF再投资−预测现金资本开支)`展示算术分解；NOPAT来自DCF已有FCFF与再投资加总，不称独立取数。税项、融资/投资分类、非现金投入、营运项目及不同收入预测假设仍未归因，差额不自动称为估值误差，净差额为零也不证明分类闭合。

`cash_check.status=blocked_missing_standard_history`时按报告期/字段列出`missing_periods`，预测和差额为空；选中估值的状态和退出码仍按估值本身判定。标准证据矛盾或导出失败则作为请求失败，不悄悄忽略。`check_id`绑定现金检查结果、源输入摘要、研究验证摘要与检查代码版本，并引用原`valuation_run_id`；检查随stdout交付，请保存JSON，估值归档不被修改。

不确定性证据接入已完成[两份安克主库前后对照](acceptance/cash-uncertainty-output-20260911.json)：通用情景92.1504元、零增长情景42.2846元及现金预测均不变；仅新增证据、检查代码哈希与`check_id`变化。零增长当前ID与更早归档的差异来自本次之前的政策说明更新，数值输入未变。此前并发验收发现的读写ATTACH锁冲突现已通过[只读查询复验](acceptance/readonly-valuation-20260911.json)修复：两路主库CLI并发成功，完整JSON与此前串行结果一致。估值、就绪度、WACC参考、行业资本、行情、市值及status七条查询路径只读打开已有数据库，不自动迁移或创建文件；同步与导入保留写入。此能力限于多个读进程，不承诺跨进程写入与读取并发；底层stderr现随结构化失败的reason交付，见[真实Go失败验收](acceptance/valuation-stderr-20260911.json)。本次接入验收：专项3项及Python全套368项通过，4项既有外部数据缺失跳过、7条既有警告；Go全套、构建、143个相关文档链接与五份CLI文件哈希检查通过。证据对象按调用隔离，返回值修改不污染后续结果。

[此前主库验收](acceptance/cash-crosscheck-20260911.json)保留缺2024历史的阻断结果。现已完成[真实主库副本正向验收](acceptance/cash-history-upgrade-20260911.json)：三期TDX记录→公告关联→标准物化→两期TTM→统一CLI现金检查，历史不再缺项。[差额解释及复现](anker-cash-bridge-20260911.md)单独记录。现金预测OCF为16.4115亿元，沿用资本开支3.1963亿元，现金代理13.2151亿元；DCF首年FCFF为6.4163亿元，差额−6.7988亿元，仍标记未分类，不能据此修改估值。原run ID和92.1504元条件值完全不变。

`evidence.research_scope_audit`提供[起点子组复核](../valuation/research/tdx-operating-cash-forecast/scope-review.md)的摘要哈希及限制；它使用已评分样本，不代表当前公司获得单独预测有效性批准，亦非完整DCF准入。子组年度WAPE可能退化，金融信号零值不替代业务原文核验。

现金检查另输出`forecast_basis`（两侧收入及增长、现金模型和资本开支规则、净再投资/现金资本开支口径及维持性投入未单独估计状态）与`revenue_only_sensitivity`。后者只把现金模型收入替换为DCF首年收入，保持平均OCF率、现金资本开支及DCF结果不变，明确标为未经验证的敏感性；不替换`cash_forecast`，不称FCFF。缺少或非正/非有限的DCF首年收入拒绝比较；历史缺项仍优先返回原阻断。真实安克敏感性及未闭合税费/再投资分类见[现金桥接报告](anker-cash-bridge-20260911.md)。

迁移038只扩展FN114到2024-06-30；其他字段审核有效期不变。真实副本重开重放增改删均零，其后已[备份发布主库schema38](cash-main-publication-20260911.md)；主库重开重放及统一CLI正向验收通过，原备份哈希不变。默认Go/Python回归使用自包含真实样本；1GB限制下的完整主库副本验收另行记录，不依赖CI保有本机数据库。该历史为后来取得的版本，原公告日期边界有验证，但不冒称当时已留存或严格PIT。

## JSON契约

`contract_version`固定为`alphalake-company-valuation-v1`；stdout只输出JSON，诊断留stderr。CLI语法错误仍遵循argparse的帮助/错误输出。

| 字段 | 含义 |
| --- | --- |
| `status` | 选中结果的真实状态，或身份/政策歧义等阻断；`failed_request`表示配置、文件、导出或结果读取失败。 |
| `code`、`security` | 请求代码及本地标准证券ID、市场、名称、财务状态和缺项；身份不能唯一定位时无`security`。 |
| `report_period`、`information_as_of` | 明确请求的财报期及带时区信息截止；不是运行当前时间。 |
| `readiness_sha256`、`universe_count`、`universe_scope` | 完整扫描快照摘要及本地集合分母；实际只计算所选证券。 |
| `selection` | 选中的`policy_version`和理由；选中阻断政策也保留，`fallback_applied=false`。 |
| `valuation` | 成功时为紧凑估值摘要，否则为`null`；不能把其他候选的成功值填到这里。 |
| `candidates` | 每份配置的版本/摘要、类型、实际配置政策、路由、状态、缺项/原因及成功摘要。 |
| `cash_check` | 仅显式请求时返回；现金预测及差额、独立状态、缺项、check ID和血缘，不改变选中估值。 |
| `boundary` | 条件估值及显式政策集合的适用范围。 |

成功摘要含`run_id`、引擎摘要、政策ID/情景、财务与信息时点、`value_per_share`（数值、CNY、CNY/share）、经营企业价值（百万人民币）、WACC（小数比例）、资本结构依据、股本依据、股权桥接、假设、边界及证据文件路径。仅经营价值模型的每股值可以为空，不能当成0。`share_date`仅在财报日股本桥接时直接使用财报期；期后股本情景留空并按运行请求中的市场股本证据追溯，不猜日期。

就绪度、参考选择和导出子进程失败时，单公司JSON的`reason`保留退出/超时说明及stderr；逐公司导出失败仍为`failed_execution`，请求前置失败为`failed_request`。批次逐公司失败继续写入报告；批次整体无法开始时以退出码1及stderr说明原因，不产生成功报告。

退出码：`0`选中条件估值成功；`2`身份/数据/政策等业务阻断；`1`运行或请求失败。即使退出非零也先读JSON状态，不把业务拒绝当网络故障自动重试。`--policy`的版本名必须唯一；相同名称的不同内容不能混为候选。

完整请求、输入、政策、参考和计算结果仍按原机制保存到`ALPHALAKE_VALUATION_RUN_DIR`，默认`valuation/backend/data/alphalake_runs`。入口核对保存运行的请求/引擎内容标识及返回值，再输出摘要；这些完整文件不是新建的一套估值事实库。

已支持[显式一年期利润校准政策](forecast-calibration.md)：沿用同一入口及运行记录，只按批准的公司、报告期和可用时间生效；没有新增自动政策选择规则。

## 当前边界与验证

这是**实时计算并展示候选**的CLI入口，不是只读检索全部历史结果的服务；重复请求复用同一标准导出供候选计算，但仍重新执行引擎。已有[估值使用Skill](../skills/alphalake-valuation/SKILL.md)指导调用与结果解释；另有[只读历史查询](valuation-run-query.md)发现已有run ID，以及[运行比较CLI](valuation-comparison.md)输出差异及限定WACC归因；尚无多因素贡献分解、自动政策批准/跨期延用或MCP。仍使用现有全本地集合就绪度扫描，单公司首次查询可能较慢；没有新增缓存或常驻任务服务。

离线回归使用真实Go标准链导出的安克/茅台输入，分别复现153.50/1269.02元；行业路由部分为显式合成分类，不冒称新的来源行业验收。覆盖候选顺序不影响结果、同级歧义、显式选择、过期专项不降级、未知证券、重复版本及保存请求篡改拒绝。新增入口不改变任何模型公式、数据库事实或全市场完成率。

真实CLI验收见[运行摘要](acceptance/company-entry-20260910.json)：

| 数据库范围 | 公司 | 默认处理结果 |
| --- | --- | --- |
| 全市场主库 | 安克 | 专项缺审核附注，`blocked_missing_inputs`、退出2；行业候选126.14元仍单列，不降级。 |
| 全市场主库 | 安徽凤凰 | 唯一行业候选9.14元、退出0；与此前验收run ID相同。 |
| 审核隔离库的迁移副本 | 安克 | 专项153.50元、退出0。 |
| 审核隔离库的迁移副本 | 茅台 | 专项1269.02元、退出0。 |

隔离原库SHA在复制前后及验收结束保持相同，未改原研究库；主库仅只读查询。主库安克行业候选也与既有run ID相同，说明统一入口没有改变该模型输入或公式。不能把隔离库有附注的成功结果当成主库专项已自动补齐。

验证：Python全套239通过、4项既有外部环境跳过；新增入口定向回归覆盖最终错误JSON字段。`go test ./...`、构建、`go vet ./...`及文档链接/差异检查通过。无依赖、迁移或Go代码变更。


## Skill使用验收（2026-09-10）

`skills/alphalake-valuation`仅含工作流说明和客户端展示元数据，沿用上述CLI；不添加包装脚本、模型或依赖。入口说明以本文为准，Skill按需读取，不复制完整数据契约。源目录已用软链接安装到本机技能目录，其他环境安装见根README。

已通过Skill frontmatter/命名校验、展示元数据YAML检查；按Skill命令从`/tmp`切换到后端目录，使用既有审核隔离库复现安克153.50元（退出0）。测试用过期政策返回`rejected_input_or_policy`、空估值及退出2；另核对既有主库安克“专项缺项、行业成功”的结构，指导不得降级。产物在本地`workspace/skill-acceptance-20260910/`。

这是本地命令和结果契约的实际检查，没有独立agent行为评测或外部客户端加载验收；不新增财报原文审核或全市场估值覆盖。未改动程序，未重跑Go/Python全套测试。

WACC来源解读：`approach_used`为`direct`、`industry_average`或`decile`时，`capital_structure_basis=not_used`表示未用公司资本结构计算该WACC。不要把这些分支的兼容占位权重当成公司市场权重；具体方法核对及旧/新run ID边界见[方法审计](damodaran-method-audit-20260911.md)。

## 经审核的标准资产加回

显式公司分配可使用`nonfinancial-reviewed-history-fcff-v1`，审核绑定及真实验收见[已审核资产链路](../valuation/research/reviewed-assets-20260917/README.md)。该政策仅增加指定的已审核标准资产；FN19/FN431允许以`nonoperating_partially_restricted_financial_asset`分类绑定`restricted_component`（item/import_sha256/evidence_sha256），先从TDX总额扣除同原文已审核受限额，再乘显式回收率。其余混合科目不支持拆分，未审核资产不自动计值；失效证据不会降级为默认行业政策。统一摘要通过`method_assessment.equity_bridge.reviewed_assets`保留采纳值、标准源和原文补充。

## 增长与资本联合审核及自动重估

统一摘要的`method_assessment.growth_capital_consistency`提供标准TTM研发/购建现金、逐年NOPAT变化分解、净再投资/FCFF及资本释放/资金来源提示。公司资本效率仍为待核验代理，不用计算通过证明输入合理。批次`--previous-report`及刷新入口的`incremental-state.json`负责先验证再复用、变化重算与失败恢复；字段、命令、两公司边界及实际验收见[自动重估说明](../valuation/research/automatic-valuation-20260917/README.md)。单公司输出的`universe_scope=local_security_code_candidates:<code>`和count只表示该代码候选，不能当全市场分母。
