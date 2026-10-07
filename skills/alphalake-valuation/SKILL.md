---
name: alphalake-valuation
description: 使用 AlphaLake 已有数据库和显式政策进行 A 股公司条件估值，解释专项与行业候选、缺项和来源证据。适用于公司估值、故事条件与修订、历史估值发现、已有结果解读、两次估值比较、具名条件交付和口径核对；不用于一般股价预测、仓库开发或全市场同步维护。
---

# AlphaLake 公司估值

解释数据期间、单位、缺项和接口适配前，读取`docs/valuation/data-contract.md`。标准累计TTM通过已有prepared入口消费，季度缺项不等于TTM缺项；`quarters_since_10k=0`在prepared路径表示关闭重复轮转，实际期间看prepared证据。普通标准TTM与专项调整TTM分别处理，年度研发队列仍独立检查。

已有专项／行业政策使用`tools.company_valuation`；原生完整条件用下方具名交付，业务故事用“故事估值与修订”入口。把结构化结果解释给用户；计算、身份／单位／时点及政策准入由程序执行，不在Skill中重写DCF、补数或另选数据源。

当前SQLite财务契约为v11，报告血缘通过共享记录及标准事实引用恢复，旧快照须显式重导出，不只改版本号。财务为现有标准快照，不是历史PIT快照；公告日期缺失或未匹配CNINFO不代表数值缺失，不要求用户先补公告。报告财务期、快照版本与实际缺项，不能把当前取得的修订数据说成当时已知。

## 准备调用

- 定位AlphaLake仓库：优先使用会话已知路径或`ALPHALAKE_ROOT`，否则检查当前目录及父目录的`valuation/backend/tools/company_valuation.py`。安装Skill的目录不等于仓库目录。
- 首次使用读取仓库内`docs/guides/company-valuation-entry.md`；需要准备行业政策或理解完整度时，再读`docs/history/a-share-automation-acceptance-20260910.md`。文档中的本机路径、报告期、估值数字是验收示例，不是默认配置。
- 从用户请求和已确认配置取得：六位证券代码、财务库、报告期、带时区信息截止、已安装后端依赖的Python、一个或多个`BatchPolicy`文件；参考驱动政策还需匹配参考包或参考库。公司名称、代码身份、数据库范围不明确时先核实，不猜代码或按价格挑库。
- 复用会话已经授权和确认的输入；必要参数确实缺失时只询问缺失部分，不重复请求许可。不要把单公司Policy JSON直接传给需要BatchPolicy的`--policy`；包装方法见上述入口文档。
- 普通公司估值请求只计算并保存估值运行；不顺便同步全市场、升级/替换数据库或修改政策以消除拒绝。当前入口会计算所有候选并写入运行文件，并非只读历史检索。

## 执行

下面变量由已核实配置设置，使用绝对路径；在仓库后端目录调用，避免安装位置或shell当前目录影响导入：

```bash
cd "$ALPHALAKE_ROOT/valuation/backend"
"$ALPHALAKE_PYTHON" -m tools.company_valuation "$ALPHALAKE_DB" "$ALPHALAKE_CODE" \
  --period "$ALPHALAKE_PERIOD" --as-of "$ALPHALAKE_ASOF" \
  --policy "$ALPHALAKE_POLICY" --alphalake "$ALPHALAKE_ROOT/alphalake" \
  > "$ALPHALAKE_RESULT"
```

需要时添加重复的`--policy /absolute/other-policy.json`及`--reference-database "$ALPHALAKE_DB"`。内嵌参考包与参考库参数互斥，按实际配置选择；不要静默删除绑定。`--select`接受传入配置的`policy_version`，仅在用户已明确指定或已确认的选择规则适用时使用，不按候选价格高低选择。

保留退出码，并在非零退出后仍读取输出JSON及stderr。标准财务从当前宽表查询，源位核验依赖与数据库配套的不可变ZIP；归档根不同于数据库目录时显式设置`ALPHALAKE_WORKSPACE`，缺失或哈希不符须报告，不回退到旧库。依据真实进度和入口超时排查，不并发反复重跑同一查询。解释器/二进制/依赖缺失应报告具体环境缺项，不能改用样本脚本算一个数。

## 解释结果

先检查`contract_version=alphalake-company-valuation-v1`、证券代码、报告期和截止是否匹配请求，再读取顶层`status`、`selection`和`valuation`；不要从`candidates`挑一个成功值冒充默认结果。

| 结果 | 处理 |
| --- | --- |
| 退出0、选中估值成功 | 报告所选模型、每股值及币种、财务/信息时点和股本依据；WACC是小数比例，展示时转百分比。仅经营价值模型可能无每股值，保持为空。 |
| 退出2、业务阻断 | 列出具体身份/来源/缺项/政策问题。专项缺项或过期时即使行业候选成功也不降级；可以单列其候选值和不同口径。 |
| `blocked_ambiguous_policy` | 说明同级候选及差别；缺少选择依据时请用户选择，不通过文件顺序或最高价格裁决。 |
| 退出1、运行失败 | 读取`reason`、`error_type`及stderr定位配置/导出/环境问题；不要把失败当成估值0，也不无限重试。CLI语法错误可能只有argparse文本。 |

必要时读取`valuation.evidence.run_file`或候选对应运行文件中的`request`、`audit`和`report`，按`run_id`追溯。先读摘要，按问题展开证据，避免把整个财务快照或全部历史运行塞入上下文。文件缺失如实报告，不根据文档数字伪造运行。

解释原生网页发布数据时，可读`workspace/derived/system-delivery/valuation.sqlite.delivery.json`所指运行中的`freshness.json`及`methodology`摘要：分别报告报价日、股本基期、财务缓存/上游核验状态与参考版本日期。发布范围以该收据及每轮冻结的`codes.txt`为准；`--published-scope`保留完整现行范围，不按成功筛选。报价来源partial与整批未完成分别报告，完整响应可从该轮`.tar.gz`及逐文件哈希清单追溯。固定旧截止的重放不代表今日最新；方法审阅不自动批准默认假设，发现数不等于数据缺项数。当前研发阶段保留手工刷新，不因估值请求安装定时任务。

解释实际forecast参数时读取报告的`dcf.forecast_parameters`（值、来源、条件及是否被年度路径覆盖），保留`inputs.valuation_assumptions`作为原输入。空输入不等于未计算，显式0不等于缺项；不另用15%或年度未调整利润率补显示。终值组成直接读DCF输出；2.5倍、基期持平、行业参考带及预设均不自动成为合理公司预测。数据库新会话/无覆盖的准备入口现采用来源初值v1：合格同窗同比、持平调整后利润率及匹配US行业倍率；H=1还提前税率/WACC过渡。历史/行业代理不是公司预测，完整显式API不被重写；空值回退及保留融资见`docs/valuation/default-assumptions.md`。完整优先级与本地过渡契约见`docs/valuation/data-contract.md`；旧报告用原提交或显式重建，不混用新旧响应。

回答通常给一张简短结果表，加关键假设/阻断和运行引用。不得把条件估值称为当前目标价；`share_date=null`不等于股本日期就是财报期。区分主库与审核隔离库：另一库拥有专项附注，不证明当前库已补齐。

## 发现历史运行

用户未提供run ID时，先读`docs/guides/valuation-run-query.md`，从已确认的运行目录查询；不要求用户查找哈希，不为了获得ID重新计算。

```bash
cd "$ALPHALAKE_ROOT/valuation/backend"
"$ALPHALAKE_PYTHON" -m tools.list_valuation_runs "$ALPHALAKE_CODE" \
  --run-dir "$ALPHALAKE_RUN_DIR" --period "$ALPHALAKE_PERIOD" \
  --latest-per-model > "$ALPHALAKE_HISTORY"
```

目录和模型可用重复的`--run-dir`、`--model`限定；`--as-of`是带时区的信息截止上限。仅使用已确认目录，不递归搜索整个workspace或静默切到另一个库的结果。

检查`contract_version=alphalake-run-query-v1`、查询条件及`status`。退出2/`partial`或`latest_selection_complete=false`时先报告具体不完整原因，不断言哪个最新；`no_matches`只代表配置范围内无匹配。退出1报告请求/环境问题。

`has_more=true`时按`offset/limit`继续取页，取得所需候选及元数据。只有模型组的`unique_latest_run_id`非空且证券、模型、期间、情景、资本口径符合用户意图，才可直接选取；从对应`results[].locations[].path`取得比较目录。最新按`information_as_of`，不是计算创建时间；同截止并列时展示模型、情景、WACC、资本口径及证据差异，请用户明确业务口径，不按文件时间、ID顺序或估值高低裁决。即使价格相同，不同run ID也不是重复记录。

查询只验证保存请求与引擎哈希，摘要金额尚未重算；需要核验两端报告和解释差额时再调用比较入口。记录扫描不是原子快照，运行目录有并发发布时重新查询。

## 比较已有估值

需要比较两个已知运行时，先读仓库的`docs/guides/valuation-comparison.md`，使用已有run ID及其实际目录：

```bash
cd "$ALPHALAKE_ROOT/valuation/backend"
"$ALPHALAKE_PYTHON" -m tools.compare_valuations "$ALPHALAKE_BEFORE_RUN" "$ALPHALAKE_AFTER_RUN" \
  --run-dir "$ALPHALAKE_RUN_DIR" > "$ALPHALAKE_COMPARISON"
```

另一端不在同目录时提供`--after-run-dir`。命令从保存请求在当前引擎重算核验，但不访问数据库、不写新估值。未找到运行或不能复现历史报告时报告拒绝，不照文档数字拼接结果。

检查`alphalake-valuation-comparison-v1`及`attribution.status`。只有`verified_wacc_only`可引用其WACC贡献；`not_attributed`只说明变化因素与总差额，不能全部归于WACC。同时列明双方模型、财务/截止/股本口径、标准ID差异和规范化变化；差异被截断时按JSON Pointer到原文件查明，不把差异条数当作金额变化条数。

若仅解释一个已有运行，直接读取其已保存证据并注明原时点，不必启动一次新计算，也不能声称旧运行代表最新数据。

默认批量范围仅沪深市场；北交所须用户明确指定。批次与覆盖率分母不得默认纳入北交所；显式公司查询保留既有历史证据。

当前默认主库为项目workspace/alphalake.duckdb，财务和达摩达兰参考数据共用此库；动态输出仅写workspace/derived，默认运行目录为workspace/derived/valuation-runs。历史日期目录只作迁移记录，不作为运行配置。


## 用户完整输入的条件交付

用户已要求或授权具名条件比较时，先读`docs/guides/company-valuation-entry.md`的“原生具名条件自动交付”，使用既有`tools.evaluate_native_policy --explicit-input`。已有完整请求使用`--explicit-input`；仅有证券及已确认参数时使用`--prepare-inputs --ticker`，可选`--overrides`按证券提供原生假设点路径，复用SQLite取数及PATCH，不手工拼财务。两种模式互斥，不需要BatchPolicy或新前端JSON表单；不替用户指定信用、增长或资本倍率，不借样本参数凑齐。核对`protocol.input_preparation`来源、继承默认与覆盖，保留准备失败分母；`user_explicit`在准备模式只是基线名，不表示所有字段都由用户提供。

重复`--explicit-input`在单进程处理已确认范围，使用新的`workspace/derived`输出目录，按项目资源隔离规则运行；可用`--web`核验现行API。先读`native-conditional-delivery-v1`的`summary_file`，保留`user_explicit`、全部条件、拒绝及原因；`selected_scenario=null`是预期结果，不要求选最高/最低/中间值。收入规模、FCFF与终值检查不是预测认证，归档参考不称今日WACC。该run ID在本次目录追溯，不传给旧估值运行查询或比较入口。

已批准报表信用附加条件使用同一入口的可选`--reported-credit`及已有显式政策，参考从同一SQLite读取，先读`docs/valuation/native-cny-policy.md`已批准规则。不从统一BBB、净财务费用或零利息推信用；保留原用户结果，解释表内标签及两种初始/稳态条件差别。缺明确CNY参考融资组件、范围或匹配版本时报告原因，不自动切原生详细/default分支；不能称代理为公司正式评级或新市场WACC。

研发税务对照先读`docs/valuation/methodology-contract.md`的研发契约。已有显式`research_tax`路径优先；需要保持当前研发费用率的条件时，在同一具名交付入口加`--research-tax`。该条件只用普通扣除和原税率代理，原用户基线保留；不自动开启默认、不从全部费用认定加计扣除。解释`operating_tax_basis`、逐年税基/现金税/NOL、稳态及过渡终值，并指出无到期/限额抵亏和实际选择的融资/比例代理；金额变化不证明预测更准。不混用旧C/E现金税比例与新税基税率，不额外扣研发再投资。

## 故事估值与修订

用户要求业务故事到估值时，先读`docs/valuation/narrative-valuation-plan.md`和数据契约的“叙事到年度输入契约”，使用`tools.narrative_valuation`。已确认同版SQLite、范围收据及证券后`prepare`生成待审草稿；只把用户明确条件或有依据、明确标记的研究判断写为`conditional`。记录证据类型、文件哈希和定位，每项经营／资本／研发／融资／终点条件绑定主张、反例和修订触发。不能将准备时的默认增长当成采用的公司故事，不能从用户一句话补出获认证的参数。

`run --database ... --stories ... --output ...`只计算文档指定故事，可用`--web`复验实际API；同公司多个故事保留，无概率或胜出者。先读取`narrative-valuation-v1`摘要及中文`report.md`，run ID由工具生成，区分草稿、范围外、不适用、来源受阻与计算拒绝；经济依据不足和算术通过分别解释，不覆盖事实、股权股本输入或网页默认。

用自身`verify RUN_DIR`核验归档、重新编译和重放；修订写`previous_run_id`并增加改动故事的`revision`，运行到新目录后`compare BEFORE_DIR AFTER_DIR`。收据写在运行目录外，不把故事run ID传给旧历史查询／比较工具。新数据或合法口径变更重新准备基线、保留旧证据并解释输入差异；经济自动选择、短历史估计和新引擎分支仍延期。

## 原生跨公司条件情景

需要自动生成假设而非已有专项政策估值时，先读项目`docs/valuation/native-uniform-scenarios.md`。复用`tools.evaluate_native_policy --selection-policy`，输入已确认的SQLite、统一配方与证券列表；人民币曲线及达摩达兰参考从SQLite绑定发布读取；不为单家公司改规则。结果在`summary.json`，以原名单为分母，先区分`blocked_inputs`、`blocked_policy`和`selection.status=rejected`，再读取`scenario_generation`与`scenario_*`结果。

`selected_scenario=null`表示没有自动推荐；3/5/10年持续期及历史利润率目标是条件假设，不按最高、最低或最接近股价挑选。缺少可比连续利润率时，历史目标场景缺席，不能用行业未调整利润率补位。来源类别、全部实际输入、预测规模/再投资/隐含回报随结果保存。用`tools.review_native_policy`重放整个目录；历史预测诊断不是完整DCF准确率，也不是严格PIT或独立留出。

历史研发补充见`docs/history/native-research-cohort-bridge.md`：`historical_research_expenses`只保留完整利润行以外的年度研发观测；解释缺项时分别检查利润行与研发队列，不把独立研发年份当成完整财务年度，也不从不同政策EBIT口径借值。

当前暂停为回测扩展早年采集。解释条件估值优先读取`economic_checks`及`growth_duration_effects`（`docs/valuation/capital-efficiency.md`）：核查再投资/回报与终值过渡，不把高增长当利好，不将等价资本倍率自动设为公司参数；历史缺项不是现有条件场景的新增门槛。

比较资本效率过渡时读取`docs/valuation/capital-efficiency.md`，复用`tools.compare_native_capital`及当前已核验情景目录。该工具只改显式`annual_sales_to_capital`，保留其他假设；终点是原终值政策的代数配套，不是新发现的公司资本效率。报告再投资和条件值变化，并保留原12家缺项/拒绝，不按价格选择过渡情景。`--basis company-history`另以最近连续三年收入合计/含账面少数股权的研发调整资本合计执行完整DCF单因素对照；当前v3直接使用`consolidated_book_equity`，不再另加少数股权；不得将股权桥接的市场少数权益当作账面资本，缺项不归零；缺年、非正资本或租赁/专项prepared TTM口径不匹配则保留原因，不自动改用公司代理。

显式总资本预算使用`inputs.valuation_assumptions.annual_net_reinvestment`，按实际投资年、百万元报表币种供给完整预测年数；与`annual_sales_to_capital`互斥，不再按销售领先期移动，标量倍率不消费。总预算已含资本化研发净投入，不再加研发或营运资本；零／负预算保留，公司依据另审。终值仍按g/ROIC，末期衔接由现有审阅器诊断，不能自动延长预算或设置投入下限；无预算时保留原路径。完整请求／PATCH及清空规则见[数据契约](../../docs/valuation/data-contract.md#显式年度总净再投资)。既有周期／资本倍率／规则候选工具不替换用户已填预算，完整显式条件可直接交付。

沪深300周期识别／重估复用`tools.csi300_valuation`，先读[限定识别契约](../../docs/valuation/cyclical-valuation.md#沪深300限定识别与重估)。使用同版官方300只名单、主库范围审核、SQLite及显式业务证据；申万细分和业务机制用于本地识别，不将4,090家宽泛风险观察范围称周期股。固定3年算术条件只作主表展示，其他条件和原默认保留，无自动胜出者；周期条件受阻不回退默认。金融保留范围外，其他公司保留原生路径。说明股本／财务期、参考、收盘日及资本释放等限制，不把本轮结果自动发布成网页默认或公司目标价。

资本倍率适用性核对复用`tools.review_native_policy`的`capital.proxy_comparison`：只看最新完整财年起五年，模型与独立SQLite不混合；历史存量倍率反事实只量化投入敏感性，不作为获批未来效率或完整DCF，缺年不延长窗口。参见[资本核对](../../docs/valuation/capital-efficiency.md)。

原生入口统一使用标准财务出口，不再自动启用安克/苏泊尔专项EBIT。来源EBIT可用不证明经营范围已闭合；比较旧结果须同时核对利润、现金、投资及恢复的历史行，并按当前政策重建，不能只把差额归因于EBIT。

解释原生准入与经济合理性时读取`docs/valuation/methodology-contract.md`：兼容API的`input_contract`说明所选方法可消费的输入；审阅结果的`methodology`区分经营范围、再投资与终值风险。`ready`或`numerical_replay=passed`不代表公司假设获批；负再投资需说明资本释放假设，单因素终值对照不称概率区间。亏损税盾解释须读取`dcf.wacc_tax_shield_adjustments`、`tax_shield_basis`、`unused_nol_at_terminal`及`terminal_nol_treatment`；普通及研发条件均已在税务尾部承接剩余NOL，不再报告“普通路径未计价”。读取新增年度/长期`debt_tax_shield_spreads`和可选`inputs.valuation_assumptions.debt_tax_shield`；未来正常化WACC/债务分量由完整显式路径供给，否则保留固定初始分量。整体WACC不猜拆，比例代理不等于边际扣除；真实到期/限额、融资与亏损余额未自动核实。已有完整未来融资路径用显式交付入口保留；旧两阶段参考准备、自动选择或信用附加条件不得替换它，相关限制不等于原估值被拒绝。

自动假设选择采用`native-assumption-selection-v2`：盈利收缩可生成条件恢复情景，必须同时说明恢复持续期与负再投资回收依据未建立；不把负再投资当已实现现金、不自动采用场景，亏损仍需独立扭亏政策。旧政策运行按原提交复验，当前规则见[自动选择](../../docs/valuation/native-assumption-selection.md)。

可转债只按[市场索偿契约](../../docs/valuation/methodology-contract.md#可转债索偿契约)解释：读取原生`convertible_claim`或专项`report.convertible_claim`，核对B/O/M与原已扣账面额。主值扣纯债和权利一次再除现有股份，潜在转股股数仅诊断；禁止恢复终局取低、以账面其他权益代替含权市值、扣期权后再次全转股摊薄。含权报价/可比纯债收益率/现金流未提供时不能宣称完整定价；非详细WACC仍为显式融资条件，当前默认不自动启用。
