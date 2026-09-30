# 架构边界与估值引擎调整台账

核对日期：2026-09-29；代码截止`4568cf4`。本页是引擎变更的维护入口，实际方法说明见[方法契约](methodology-contract.md)，当前数据供给见[项目状态](../implementation-status.md)。**本页完整列出可追溯范围内的改动，不宣称全部本地政策均已获达摩达兰原文认证。**

## 责任边界

| 责任方 | 应交付 | 不应承担 |
|---|---|---|
| AlphaLake数据层 | 来源证据、标准财务、期间/单位转换、确定性财务派生、行业/国家等参考、缺项及版本 | 为产生估值而改财务事实；把缺项归零；按公司偷偷替换利润/资产 |
| AlphaLake假设与适配层 | 显式增长/利润率/资本效率/税率/风险假设及依据；组装既有SQLite/API输入；用户覆盖、来源和政策版本 | 将行业均值当公司事实；把本地情景说成教材结论；在接口外另算最终DCF |
| valuation | 消费输入，执行研发/租赁调整、WACC、FCFF、折现、终值及股权桥接，保持原网页交互 | 承担采集、逐公司源修补；以新增估值分支掩盖数据未达标 |

主线是“整理事实与假设 → 固定输入契约 → valuation估值”。目录位置不等于职责：现有`valuation/backend/tools/`、`data_sources/`中承担导出、参考供给和假设组装的代码仍属于数据/适配职责，本轮不为分层另做目录搬迁。

把valuation作为稳定估值系统维护，不以完善数据为名持续开发引擎。当前引擎冻结：仅修明确BUG；必要接口扩展、算法和模型政策调整先进入[待审批](../../valuation/TODO.md)，未经明确审批不得实施，不能混在数据补齐提交中。既有实现、既有授权和测试通过均不自动证明方法正确。

2026-09-30数据适配提交`442c39b`：补入既有官方国家风险及税率文件的Israel行，国家参考范围随SQLite声明，WACC参考导出及消费校验同步为四国13条记录。未修改`engine/`、国家选择、权重、ERP计算公式或税率政策；来源仍为官方[国家风险表](https://pages.stern.nyu.edu/~adamodar/pc/datasets/ctrypremJuly26.xlsx)及[国家税率表](https://pages.stern.nyu.edu/~adamodar/pc/datasets/countrytaxrates.xls)，不是新增估值方法。原100家实际输入、最终值及旧3,961个参考值均不变，安道麦的国家参考供给闭合但仍缺行情；不能据国家标签认证其实际地区风险暴露。显式人民币WACC国家权重接口仍限CN/HK/US，未扩展；冻结旧请求按原提交复验，当前输入须显式重新导出。发布及正负验证见[当前状态](../implementation-status.md)。

2026-09-30数据适配提交`d44f049`：经用户授权，将原文已审核同一法人的A/H来源关联接入主库、SQLite及原生参考默认取数，精确证券源记录和显式用户覆盖优先；会话来源增加审核/原文定位，不改变公式、预测、US默认方法或参数。复用官方[公司行业名单](https://pages.stern.nyu.edu/~adamodar/pc/datasets/indname.xls)的原H股行业/国家行，关系证据来自CNINFO年度报告公司信息；这是数据关联依据，不是新的达摩达兰模型规定。行业经济适用性未自动认证，缺行情继续拒绝。原100家实际输入和估值零变化，额外两家参考齐备但行情未闭合；发布、撤销与来源版本规则见[系统交付](../guides/system-delivery.md#同发行人参考的默认关联)。

## 可复验范围

- 本地导入基线：`0b29f42`；逐提交遍历其后至`4568cf4`，共**27个触及`valuation/backend/engine/`的提交**，净差异11个文件（10修改、1新增），425行增加、198行删除。行数不是方法数量，也不是质量指标。
- 导入前已含`prepared_ttm`和缺失传播修改。[来源记录](../../valuation/UPSTREAM.md)列上游`c4b86e5`、本地复验`7ce156a7c6d568d41f480919d84b998bee599d54`。本仓库没有合并原Git历史，不能把导入树当未经修改的上游，也不能认证导入前全部差异。该部分明确列为待补基线证据。
- 台账还列出目录外影响估值的适配/假设边界。未改动的原模型分支不在“已审核通过”范围；本轮不是对整个上游系统的数学认证。
- 网页曾引入TDX专页/政策表单并限制原交互，`58b50d3`已恢复原流程；此后前端源码至截止提交无变化。相对导入基线仍有16个源码文件差异，主要为类型/空值/提示及演示数据修复。详见[原生偏差审计](../history/valuation-native-integration-spec-20260924.md)。

## 依据与结论如何使用

**公式修正**表示所述关系有原始方法依据；**契约/工程**表示保护输入和计算完整性，不是教材新增估值策略；**条件政策**表示公式可承载该情景，但公司参数/路径须有独立经济依据；**待处置**表示已有运行实现，依据不足以认证其为达摩达兰标准方法，不因记入台账而追认。

| 编号 | 达摩达兰原始资料与定位 | 支持范围 |
|---|---|---|
| S1 | [Damodaran on Valuation教学资料](https://pages.stern.nyu.edu/~adamodar/pdfiles/DSV2/DSV2.pdf)，Cost of Capital、levered beta；[行业WACC数据定义](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/wacc.html) | 资本权重、税后债务成本与Beta；数据均值不是公司最优资本结构 |
| S2 | [Country Risk](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/CountryRisk.htm)，公司国家风险暴露与成熟市场ERP/CRP分解 | 注册地不等于经营暴露；须声明所选风险模型，不重复加入总ERP中的CRP |
| S3 | [Research and Development Expenses](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/R%26D.htm)，A Reclassification of R&D Expenses及盈利/现金流影响 | 研发资本化、历史费用摊销、税收利益；不规定全行业统一五年寿命 |
| S4 | [Return on Capital / ROIC / ROE](https://pages.stern.nyu.edu/~adamodar/pdfiles/papers/returnmeasures.pdf)，投入资本定义、合并范围和期初资本 | 利润与资本匹配，历史回报不证明未来回报。此前已核对；本轮网页抓取超时，不声称重新下载成功 |
| S5 | [Growth companies: Value drivers](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/littlebook/growthvaluedrivers.htm)，再投资与收入/资本比率 | 增长要有再投资；存量比率外推未来效率仍是估计 |
| S6 | [Excess Returns and Terminal Value](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/termvalueexreturns.htm)，稳定期增长、再投资及超额回报 | 稳态再投资率=g/ROIC；ROIC=WACC只是无超额回报情景 |
| S7 | [More on effective tax rates](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/taxrate.htm)，Effective versus Marginal、The Effect of Net Operating Losses | 正常化税率、亏损抵扣与债务税盾联动；不指定本地比例/固定融资算法 |
| S8 | [Holdings in Other Firms](https://pages.stern.nyu.edu/adamodar/New_Home_Page/valquestions/valcrosshold.htm)、[Question 22](https://pages.stern.nyu.edu/adamodar/New_Home_Page/valquestions/a22.htm) | 非经营资产和对应收益配套、少数权益扣减；账面值不是公允价值认证 |
| S9 | [Options, Warrants and Convertibles](https://pages.stern.nyu.edu/adamodar/New_Home_Page/lectures/eqshare.htm)，Steps 1–4 | 转债分纯债与转股权利，其他权益索偿估值后扣减；不支持以两个终局取低替代期权估值 |

S1大PDF本轮浏览器因体积限制未完整载入；上述关系可通过资料公式检索与行业定义交叉定位，不声称逐页复核全部教材。工程边界如NULL传播、长度/有限值验证、版本一致性无需伪造教材出处，其所保护的财务定义在下表单列。

## 调整项台账

下表提交号均为本地Git对象，使用`git show <提交> -- valuation/backend/engine`即可逐行复核。代码路径以`valuation/backend/engine/`为根；T编号见下方回归索引。当前状态列注明后续替代，不能把历史修复后的旧缺陷继续当作当前问题。

| ID / 提交 | 调整位置与前后行为 | 依据、类型和适用边界 | 回归 |
|---|---|---|---|
| E01 `ac83be7` | `equity_bridge.py`、输入字典及编排器新增显式桥接；经营价值×权益范围＋索偿分量，比较不转股/全转股每股值取低 | S8/S9；接口扩展＋**待处置政策**。索偿配套有据，但取低不是期权定价，权益比例亦需同范围利润/资产证据；仅显式专项路径，不是原生默认入口 | T1 |
| E02 `8b7f6c9` | 字典/M2新增`reference_snapshot`：Beta再加杠杆，Ke=Rf+β×成熟ERP+国家风险贡献，WACC按所选权重计算 | S1/S2；条件输入扩展。权重、地域暴露、币种和税盾必须显式，不冒充公司市场WACC | T1/T2 |
| E03 `542688e` | 参考债务成本增加`synthetic_reference`依据标签并传入输出 | S1；契约变化。合成评级来自利息覆盖等估计，不是公司正式评级；选择与数据推导在适配层 | T1 |
| E04 `ec808d1` | 参考资本输入支持市场权益＋估计债务；校验金额与权重一致，未知分量不填零 | S1；条件政策/契约。账面或估计债务不可称真实市场债务价值 | T1 |
| E05 `1934478` | 字典/M4增加逐年增长、利润率、税率数组，替代默认路径；末年利润率/税率进入终值 | S5/S6/S7；条件输入扩展。参数界限与数组长度是实现契约，不是教材数值规定；末年税率必须满足正常化条件 | T1/T4 |
| E06 `7b7f21b` | 增加`industry_reference_weights`资本依据，目标权重分支不虚构市场金额 | S1；条件政策。行业D/E可作显式代理，非公司最优结构证明 | T1/T2 |
| E07 `5409879` | M2直接、行业及分位WACC输出区分代理依据，不再统一标市场权重 | S1；契约纠错。标签修正不补足公司市场资本结构 | T2 |
| E08 `c929884` | M4的非有限终值参数、WACC≤g由静默零改为拒绝；API对应失败不覆盖会话 | S6；公式定义域与工程保护。不为负终值/失败企业背书 | T4 |
| E09 `b7095e9` | M4非正/非有限稳定ROIC由零再投资回退改为拒绝 | S6；公式定义域保护。不能从ROIC缺项推断不需要再投资 | T4 |
| E10 `20ab67f` | M1新增研发调整税后利润函数；M3/公司指标/M5配套净再投资、期初研发资产与倍数计算，避免研发重分类凭空改变税项/FCFF | S3；公式修正。税项采用所选口径；不是公司实际税务申报重建 | T3 |
| E11 `bbf35a2` | M3历史研发资产/摊销从复用当前调整改为当年及更早费用队列；缺年留空，编排器提示 | S3/S4；公式/期间修正。不得前视借未来费用；年度队列不是精确TTM队列 | T3/T5 |
| E12 `a5fa186` | 历史增长、CAGR和资本回报按真实财年间隔；重复/不连续年度不压缩成连续历史 | S4/S5；期间契约保护。窗口平均的实现规则不称教材唯一选择 | T5 |
| E13 `7eeee09` | 当前ROIC/ROE仅使用唯一相邻年度期初资本；旋转TTM不借不匹配年度余额 | S4；期间修正。缺匹配期初数据只影响相应诊断，不额外阻断其他可算DCF | T5/T6 |
| E14 `dabe048` | `IndustryData`新增`expected_ebit_growth`，与收入增长分离 | S5及来源列定义；语义契约。不把行业EBIT增长改名当收入预测 | T7 |
| E15 `88c7d09` | M3历史ROIC缺税费/正EBT证据时不再默用21%；缺项保留 | S4/S7；数据/公式保护。限历史序列，不声称删除了引擎所有税率默认值 | T5 |
| E16 `e40bf57` | M4资本倍率非正/非有限由零再投资改为拒绝 | S5；公式定义域保护。缺参数与显式非法值分别处理，原应用缺参默认仍是政策 | T4 |
| E17 `baa1e8f` | M4缺资本不再拼0计算隐含ROIC；诊断允许NULL，正资本才计算 | S4；缺失保护。资本基期口径在E27进一步统一 | T4/T8 |
| E18 `151195a` | 删除`compute_ltm`旧包装，仅保留类型化TTM入口 | 工程清理，无新估值策略。移除包装不改变期间公式 | T6 |
| E19 `6400a72` | 删除调整资本失败后退回未调整资本；地域ERP只消费解析证据；债务实际零不再选另一债务余额；移除旧lookup参数 | S1/S2/S4＋工程约束。当前M2仍有带警告的注册地等原模型回退，不能声称所有政策回退已消失 | T2/T3/T6 |
| E20 `a001b01` | 增加可缺值季度位置类型；仅TTM实际消费窗口要求收入/EBIT；季度行情金额也执行报告币种转换 | S4所需期间/币种一致性＋工程契约。展示历史长度不等于估值门槛；不补造未使用季度事实 | T6/T7 |
| E21 `9523321` | `segment_resolver.py`不再把已含CRP的总ERP再加CRP；宏观输入明确基础ERP与独立CRP | S2；语义/公式修正。参考分解必须匹配实际选择的Ke模型 | T2/T7 |
| E22 `0fe3927` | 行业输入新增研发/租赁调整前税、后税利润率，与未调整利润率并存 | S3/S4；语义契约。范围/寿命不同不能直接当可比公司目标利润率 | T7 |
| E23 `5edd736` | 完整利润行之外保留年度研发观测；拒绝同年冲突、非有限/负补充值，历史队列可消费这些年份 | S3；数据契约扩展。只补研发观测，不构造不存在的整年报表；补充入口的非负限制不证明真实负费用就是源错误 | T5/T7 |
| E24 `90239dc` | M4允许显式逐年资本倍率，验证长度与正有限值，沿用Δ收入/倍率再投资 | S5/S6；条件接口扩展。工具的五年过渡路径是本地情景，非达摩达兰规定；不自动采用 | T4/T7 |
| E25 `f10fc6f` | M4负增长终值仍按g/ROIC计算净再投资，不再强制零；拒绝g≤−100% | S5/S6；公式延伸＋条件政策。负再投资意味着可回收资本，需要回收/有限经营期依据，不适用于所有衰退或亏损企业 | T4/T7 |
| E26 `c0e16c7` | 历史所得税保留符号，不取绝对值；修正资本加权回报、会计税费/现金税措辞；倍率2.5标为原应用默认而非教材常数 | S4/S7；符号纠错/标签修正。历史税收利益不可自动外推永久；同提交删除公司EBIT覆盖在适配层 | T5/T7 |
| E27 `4568cf4` | 显式合并账面权益进入TTM、历史/预测及公司资本诊断；不重复加少数股权，不自动整桶减投资；公司ROIC共用匹配期初口径并避免重复FY0 | S4/S8；资本/期间修正。账面少数权益与股权桥接估值分开；未知历史租赁资本不计算相应诊断 | T8/T3/T6/T7 |
| E27-T `4568cf4` | M4亏损/NOL按应税EBIT比例撤销初始债务税盾；负终值EBIT不获自动税收抵免；输出实际终值WACC/ROIC和未用完亏损；默认终值ROIC随实际WACC | S7支持税盾联动原则；**比例算法＋恒定初始融资分量＋终值正常化属待处置政策**。不能据此认证全部税盾时点或期权式亏损价值；整体WACC不可拆分时只提示 | T8/T4 |

E27拆两行以区分明确资本修正与额外税盾政策；提交分母仍为27。

### 回归与代码索引

测试证明实现行为及拒绝条件，不代替方法依据；以下为现有回归入口，本轮仅文档变更，不重复运行。

- T1：[AlphaLake API/真实数据集成](../../valuation/backend/tests/test_alphalake_integration.py)，桥接、参考WACC、合成债务、市场权重及逐年预测。
- T2：[M2风险](../../valuation/backend/tests/engine/test_module_2_risk.py)、[国家风险参考](../../valuation/backend/tests/test_damodaran_country_snapshot.py)。
- T3：[研发税项与现金流](../../valuation/backend/tests/engine/test_rd_tax_consistency.py)、[M1](../../valuation/backend/tests/engine/test_module_1_adjustments.py)、[M5](../../valuation/backend/tests/engine/test_module_5_multiples.py)。
- T4：[M4 DCF](../../valuation/backend/tests/engine/test_module_4_dcf.py)，终值、资本倍率和会话保护。
- T5：[M3历史序列](../../valuation/backend/tests/engine/test_module_3_cashflow.py)，研发队列、税费符号、期间及资本。
- T6：[共享编排器](../../valuation/backend/tests/engine/test_orchestrator.py)、[TTM](../../valuation/backend/engine/ltm_calculator.py)、[准入回归](../../valuation/backend/tests/test_native_input_gates.py)。
- T7：[原生假设/资本](../../valuation/backend/tests/test_native_assumption_selection.py)、[参考快照](../../valuation/backend/tests/test_native_references.py)、[准入](../../valuation/backend/tests/test_native_input_gates.py)。
- T8：[合并资本与税盾](../../valuation/backend/tests/engine/test_capital_tax_scope.py)。

11个差异文件全部对应上述条目：`data_dictionary.py`（输入/输出契约），`equity_bridge.py`（E01），`module_1_adjustments.py`（E10），`module_2_risk.py`（E02–07/E19），`module_3_cashflow.py`（E10–15/E23/E26/E27），`module_4_dcf.py`（E05/E08/E09/E16/E17/E24–27），`module_5_multiples.py`（E10），`company_metrics.py`（E10/E19/E27），`ltm_calculator.py`（E18/E27），`orchestrator.py`（桥接、期间、提示与派生刷新），`segment_resolver.py`（E21）。

## 目录外影响与已撤销调整

| 改动 | 追踪与当前边界 |
|---|---|
| TDX专项Web/API绕路 | `1f5401f`、`70a6a66`、`aebfaa5`引入，`58b50d3`撤销原生入口绕路和专页；JSON政策不再侵入原前端流程 |
| 安克/苏泊尔EBIT、现金/投资按公司覆盖 | `6084955`等接通政策曾在取数路径执行，`c0e16c7`删除；标准事实直通，不能以专项收益分类不完整为由清空整年财务 |
| 原生参考供给与会话 | `8ec6605`、`56b04e6`等使行业选择真正取值、参考随SQLite绑定会话；属于供给/交互BUG修复，非另建WACC公式 |
| 自动假设与资本候选 | `tools/select_native_assumptions.py`、`evaluate_native_policy.py`、`compare_native_capital.py`；三年资本存量代理、3/5/10年持续期、利润率保持/历史目标、统一信用档等都是输入政策，不是公司事实或教材规定。传入同一引擎，不在外部替换DCF |
| 原生SQLite导出与代理 | 财务/参考/行情通过既有[出口契约](../guides/valuation-sqlite-export.md)交付；已批准代理保留来源、范围与缺项。方法准入不能被输出窗口或一次样本成功率代替 |

2026-09-29数据接入调整：[ADR022](../decisions/022-financial-availability-and-disclosure.md)取消CNINFO公告关联对当前财务数值的前置门槛，TDX日期进入标准宽行，SQLite升级v9。修改范围为数据物化、查询与快照适配；引擎公式、默认参数及前端未改。数值变化应按实际财务输入增补解释，不记作新估值方法；执行验收以[项目状态](../implementation-status.md)为准。

### 当前估值价格时点修正（2026-09-30）

提交`0dc3285`的数据侧BUG修复：此前窗口同步与SQLite选价均错误绑定财报期末，2026H1财务只能取得6月末附近价格；用户明确当前估值须使用最近已完成交易日收盘价。现在同步以独立`--date`指定行情窗口，在线发布根据估值信息截止选择最近已完成收盘日期，导出按该窗口取最新合格未复权价格。财务和股本保持各自报告期；代理市值及既有WACC会随市场价格变化，不能要求旧数值不变。

修改位置为`internal/domain/market.go`、`internal/ingest/valuation_quote_window.go`、`internal/store/duckdb/financial_sqlite_export.go`、`valuation_quote.go`及后端导出/发布工具；不修改估值引擎、默认假设或页面。公式无变更，不新增达摩达兰方法主张；仍沿用已批准价格×报告期总股本代理及其限制。收盘取得边界依据[上交所交易规则](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml)与[深交所说明](https://investor.szse.cn/knowledge/stock/deal/t20191204_572383.html)，实际交易日期来自TDX，盘中取得记录不自动晋升为收盘证据。

回归覆盖价格日期独立于财报期末、15:00边界、旧窗口与盘中取得拒绝、信息截止限制以及既有市场代理。实际发布数量、数值影响和提交以[当前项目状态](../implementation-status.md)及其运行收据为准；代码检查通过不代表行情已补齐或网页已切换。

## 标准累计TTM接入（2026-09-30）

用户明确授权核对全部数据契约并接通TTM；原“需新增累计接口”的判断已纠正：引擎早已有`PreparedTTM`，此次只修改SQLite导出、原生后端取数/准入及直接消费方。统一spec见[数据接入契约](data-contract.md)，提交与实际数量在验收后登记，不把工作区代码当成已发布。

- **前后行为**：原入口强制由累计拆季度，再拼TTM，缺Q1会拒绝完整半年数据；v10每公司增加一个确定性TTM宽行，按全年＋当期累计−上年同月累计生成，非年末复用现有prepared入口，关闭重复轮转。历史年/季表及研发队列保留，年度路径不改，真实缺组成、冲突和单位/范围问题继续拒绝。
- **原始依据**：[Damodaran估值课程，第119张Update Earnings](https://pages.stern.nyu.edu/~adamodar/podcasts/valUGspr24/session8slides.pdf)明确用年初累计更新TTM；这是等价期间转换，不是新DCF、EBIT正常化或预测政策。源范围及追溯调整可比性仍须分别核验。
- **调用方**：`export_alphalake_sqlite.py`、`us_cn_hk_db.py`、`api/database.py`及原生覆盖/审阅/资本候选工具。普通标准TTM与专项调整TTM按来源形态区分，沿用既有选择及调整条件，不因接入prepared就错误拒绝原可比历史；行业、税率、增长、资本倍率及WACC默认规则不改。
- **明确展示BUG**：prepared路径的引擎轮转参数为0，原TTM页误显示实际经过0季、TTM等于FY0；输入页也可能借年报显示缺失TTM字段。最小修正`baseYear.ts`及两张原页面的期间/组成/NULL展示，真实季度仅作展示证据，不进入第二次轮转；无新页面、表单或交互流程。
- **回归与边界**：[累计TTM回归](../../valuation/backend/tests/test_native_cumulative_ttm.py)覆盖四种报告期、完整季度等价、缺Q1可用、缺累计项/研发年度仍拒绝及旧契约拒绝；[原页面SSR校验](../../valuation/frontend/scripts/check-ttm.cjs)已进入CI。引擎文件零改动。真实数值影响、源库不变与性能以[当前状态](../implementation-status.md)的验收收据为准。

## 不能追认为“全部符合”的待办

1. **转债取低法（E01）**：现有专项分支是保守终局对照，未估计转股权利时间价值，不能认证为S9的完整转债估值。后续明确限制为敏感性或提供与现有期权/债务机制匹配的输入；不把它扩展为全市场默认。当前代码尚未因此撤销。
2. **税盾比例与终值（E27-T）**：S7支持亏损期间税盾与现金税配套，但不直接规定恒定初始债务分量、部分抵扣比例、亏损未耗尽时立即切正常化终值。当前实现仍执行该估计并输出警告；下一次运行逻辑变更须单列政策决策及反例验证，不能记作已完成的严格方法闭合。
3. **参数情景（E05/E24/E25及目录外工具）**：路径可由模型表达不证明公司会如此发展；永久亏损、负再投资回收、末年税率及历史资本效率外推须逐项满足经济条件。没有依据时不得自动采用为推荐值。
4. **导入前基线**：补得准确上游原树后才可审计导入前全部修改；本地测试通过不能填补这段证据缺口。

本轮记录事实与约束，未调整公式、撤销已运行分支或重新发布8080。上述待办不能因文档署名、此前review或本次提交而自动转成“符合”。

## 后续变更要求

任何影响估值数值的变更，不论位于引擎、API还是假设生成器，都须在本台账增补或更新：**问题与前后行为、提交/代码位置、达摩达兰原始出处和具体关系、适用条件、本地假设、回归/反例、实际估值影响、当前状态**。工程变化注明不涉及公式即可，不编造金融依据。

发现违背方法的实现须记录；明确BUG可修复并重建基线，涉及模型选择的调整须先审批；原文只支持原则、尚不足以支持具体算法时标待处置，不先宣称符合。当前开发优先解决数据完整性、口径和输入交付；不为保持旧价格拒绝合法修复，也不把此规则解释为可以无止境扩展估值引擎。

复核台账覆盖：

```bash
git log --reverse --format='%h %s' 0b29f42..4568cf4 -- valuation/backend/engine
git diff --stat 0b29f42..4568cf4 -- valuation/backend/engine
git show <提交> -- valuation/backend/engine
```
