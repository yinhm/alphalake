# 估值数据接入 spec

本页是AlphaLake→SQLite→原生API→valuation的数据转换规范。修改解析、派生、导出、准入、会话或展示前，先核对本页及实际调用方；不能仅凭数据库列名或历史验收推断引擎要求。字段与来源映射由[SQLite导出手册](../guides/valuation-sqlite-export.md#数据范围与语义)统一维护，不再复制第二份词典。

## 职责与实际调用链

`fundamental.statement_snapshot`标准宽表→`ExportFinancialSQLiteRows`批量投影→`tools.export_alphalake_sqlite`→SQLite→`data_sources.us_cn_hk_db.fetch_company/native_compatibility`→`api.database._db_record_to_company_input`→`engine.orchestrator.run_full_valuation`。

AlphaLake负责单位、期间、身份、范围、缺项及确定性转换；valuation负责调整、WACC、FCFF、预测、终值与股权桥接。原生网页仍提交`{ticker, risk_free_rate}`，不新增政策文件或参数表单。外部文件/CIQ季度输入与AlphaLake标准累计输入是不同的源契约，不是旧版快照兼容路径。

## 期间与单位

| 数据 | 交付与实际消费 | 禁止行为 |
|---|---|---|
| 最新经营流量 | 非年末按TTM交付收入、报告EBIT及各可选流量；年末直接全年金额 | 将半年金额当全年；用营业利润替换EBIT；把累计金额写入单季位置 |
| TTM流量 | `上一完整财年 + 本年累计 - 上年同月累计`；三个组成按标准事实和系数留痕 | 为计算TTM强制拆出Q1/Q2；用零填缺项；混用不同比较范围 |
| 资产负债表 | 最新报告期末余额；不累计、不差分、不借旧年度填补 | 四季相加；把股权桥接少数股权代理当账面资本 |
| 历史年表 | 实际完整财年，保留年份和缺项；FY0不得由更早年度晋升 | 把有数据年份压缩成连续年份；用TTM覆盖全年行 |
| 研发历史 | 所选摊销期限需要的连续年度费用；独立于那些年度的收入/EBIT完整度 | 统一要求十年/六年；用当期TTM替代研发年度队列 |
| 行情与股本 | 信息截止时最近合格完整收盘价；股本仍按已披露基期；各自记录日期 | 用财报期末限制当前价格；代理市值冒充完整实时分股类市值 |
| 税率、比率、每股值 | 按原定义单独提供，年度有效税率不是预测税率 | 当普通金额求和或套TTM差分 |

标准事实金额为元、股数为股；SQLite估值宽表金额为百万元、股数为百万股；模型`PreparedTTM`明确`million_reporting_currency`和`million_shares`。价格保持元/股，利率保持小数比例。同一TTM字段三个组成必须对应同一证券、标准名称、单位、同月累计期间及合并范围，保留源版本/哈希。已知冲突期间拒绝；独立比较口径未审核的限制仍提示，不因能算而认证可比。

达摩达兰明确可用年报和季度报告的年初累计金额直接更新TTM，无需凑齐四份单季报告，见[估值课程Update Earnings，第119页](https://pages.stern.nyu.edu/~adamodar/podcasts/valUGspr24/session8slides.pdf)。例如2026H1的TTM为2025全年＋2026H1−2025H1。年末只取当年全年；Q1/H1/9M分别对齐上年同月。该转换不等于正常化经营利润或预测。

## SQLite和引擎输入

当前AlphaLake快照契约为`alphalake-sqlite-v10`，新增每公司一行`financials_ttm`；`export_cells.series=ttm`只保存组成字段/期间引用、系数、缺项及审核状态，元数据`ttm_evidence_format=standard_fact_refs`；读取时从已有`standard_facts`/审核表解析来源，不再重复持久化金额、源包哈希或逐字段血缘。原`financials_annual`、`financials_quarterly`保留真实历史，季表仍按累计差分产生，缺前季时保持NULL，不能伪造季度。`standard_facts`及原始ZIP仍是来源证据，TTM不回写成披露事实。

非年末原生后端将TTM行组装为**已有**`CompanyValuationInput.prepared_ttm`；期间为上一年同月末次日至当前报告期末，且明确币种、单位、信息截止和来源。`quarterly_financials=[]`、`quarters_since_10k=0`表示不再请求引擎季度轮转，**不表示财年后实际经过零个季度**。实际累计窗口及三个组成保存在`prepared_ttm.provenance`，真实季度展示数据单独留在`quarterly_display`证据中，不参与第二次计算；历史季表仍可从公司数据接口查询。prepared财务行的`fiscal_year`保留原入口FY0索引，TTM的真实起止以`period_start/period_end`为准，不从该索引推断披露年度。年末仍消费原年度路径，避免将完整财年误标为旋转TTM及改变匹配期初资本诊断。

`engine/orchestrator.py`已有两种互斥输入：提供`prepared_ttm`则直接消费；否则通过`compute_ltm_financials`由季度数组构建。不能同时传prepared TTM和季度轮转。此次只接通现有入口，不改引擎类型、公式、默认参数或研发摊销方法。标准累计TTM是未调整报表数据，不能与专项prepared TTM混为一类；审阅和候选工具按明确来源形态读取同窗累计证据，不改变原选择规则。

旧SQLite须从同一主库显式重导出；禁止只改版本标签、保留v9兼容别名或缺TTM时自动退回季度/年度。读取缺必要表、TTM行或单元格证据应拒绝并要求重建。主库无需迁移，来源财务数值不变。

### 缺项原因核对

`missing_standard_fact`只是SQLite目标单元格缺标准组成，不能直接解释为TDX无数或解析未完成。只读源审核须同时核对缓存与主库源定位、身份、最新标准行和拒绝规则，并按目标列出缺失期间：已有标准值、源零被策略拒绝、无源记录、映射未安装及身份/治理拒绝分别报告。累计TTM的一项缺失不能连带把其他两个已有效分量标成缺数。最新源版本NULL不从旧版本填回；公告日期不作为当前金额拒绝条件。

显式选择完整财年继续走已有年度入口，不自动将缺上年同期的TTM回退为年度。年度估值的财务与股本基期须重新标明；季度拆分、年度及TTM缺项分别计数，年度可算不计入当前TTM准入，也不代表年度默认假设已经济核验。 年轻企业及未知研发队列的处理见[ADR024](../decisions/024-short-history-and-delisting.md)：上市年限不是估值门槛，研发经济寿命不按可得历史缩短，估计输入与标准事实分层；新估计政策先审批。

### 营业收入零值边界

2026-10-01用户批准按字段放行`revenue_cumulative`的实际源零：标准事实及SQLite应保留0，缺字段、坏记录及身份/语义冲突仍拒绝；不联动放行其他收入字段。真实零收入可能存在，不等于坏数据；事实核实与当前通用模型适用是两项判断，模型不适用不得反向清空有效事实。不得用营业总收入、其他收益或融资现金流替代营业收入，也不得填微小正数、默认增长或自动年度回退以取得准入。

2026-09-30原文核验：必贝特2025年报第2页明确尚未实现营业收入，第12页营业收入金额为0.00万元，第117页合并利润表收入列为横线；2026半年报第8页同样说明尚未实现收入，第75页当期及2025H1比较列为横线。横线本身不独立证明真实零，年度明确金额及文字说明与半年文字说明分别留证。TDX的2025H1、2025全年及2026H1三个源零分量按批准后的字段政策保留为0；这是接受TDX报表字段，不声称三个期间均有独立PDF明确金额，不以PDF覆盖标准事实。[年报原文](https://static.cninfo.com.cn/finalpage/2026-04-28/1225203132.PDF)、[半年报原文](https://static.cninfo.com.cn/finalpage/2026-08-25/1225498052.PDF)。

金融报表另有口径：建元信托2025年报第68页合并利润表营业总收入为1,046,805,120.47元，包含利息及手续费佣金；其中营业收入当年列未列金额，不能把源零解释成公司没有所有经营收入。金融企业仍范围外，不为此更换通用收入字段。[原文](https://static.cninfo.com.cn/finalpage/2026-04-23/1225155338.PDF)。

冻结引擎`module_4_dcf.py`的收入预测为基期收入逐年乘以增长因子，基期为零时未来收入仍为零；有市场机会不等于这条路径适用。达摩达兰的年轻企业方法可按潜在市场、市场份额及商业化能力估计未来收入，再衔接利润率、投入与生存风险，不是规定所有零收入公司不可估值。本项目尚未批准或实现该输入路径，见[待审批](../../valuation/TODO.md)；不把当前限制冒称他的普遍禁令。[原始方法](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/littlebook/younggrowthvaluedrivers.htm)。

原文、哈希、页码提取和正向/错误哈希拒绝结果位于`workspace/derived/gap-followup/standard-chain/revenue-pdf-review.json`及`revenue-review-checks.json`；PDF保存在`workspace/cninfo/`。原文抽查覆盖当前源零类别，未逐一审核所有历史源零；本次按已批准字段规则统一重建历史，数据接通不认证零收入公司的预测适用性。

## 准入、参考与会话

准入按实际消费检查：FY0及TTM收入/EBIT、最新股权桥接、市场权重与币种转换是必需；启用研发时检查年度队列及当期TTM研发；其他调整按已选方法检查。缺可选折旧、资本开支等不阻断显式预测DCF，但相应历史FCFF/诊断保留空值。数字和对应`export_cells`状态同时检查；真实零、未知、范围代理、来源冲突分别保留。详见[准入规则](valuation-input-gates-20260925.md)。

财务信息截止不用于阻断合格当前TDX金额；历史PIT另外限制。身份、行情和参考截止分别记录。行业/国家参考随SQLite发布绑定会话，用户显式覆盖优先，US行业不得静默替换Global，总ERP不再叠加已有国家风险。现金/投资和多股类市值仍沿用已批准代理，输入接通不认证其经济范围。

参数PATCH通过共享引擎成功重算才更新会话；失败保留原会话。prepared路径的当期财务覆盖应指向`prepared_ttm.financials`，修改历史年度行只改变年度证据，不会自动改写已派生TTM。原生前端没有累计事实编辑器；不把后端显式输入要求变成新页面流程。展示TTM须使用后端结果及真实组成，不把季度轮转关闭显示成“TTM等于FY0”。

## 同发行人参考的身份取证

这是参考数据关联契约，不改变估值公式、行业代理或参考地域政策。`verify_issuer_reference`→`import-issuer-references`→主库审核快照→SQLite `reference_issuer_association`→原生默认取数；精确证券原行及用户显式输入优先。PDF只核验法人和上市证券身份，不提供这条链的财务数值。

- 中英文法人名称、目标A股及官方港股源标识、交易所分别保留原文页码、目录组织ID和文件哈希。名称、集团或业务相似不能证明同一法人。英文仅忽略空白/大小写及独立连词`and`与`&`的排印差异，不删除词语、标点或法人后缀，不使用模糊匹配；来源名称与原文名称分别保留。
- 非H股的港股结构也可核验，但原文必须明确列“港股上市交易所”和“港股股票代码”，同时核对真实代码和香港/内地交易所证据。不能将“港股报价”或普通香港地址当上市角色，不能改写原文为H股；该关联不接入港股价格或多市场市值。
- 通常目标A股代码直接从PDF取证。仅首次公开发行的招股文件、且A股证据锚点尚无六位代码时，`a_listing.identifier_source="catalogue"`显式改用已绑定的官方目录`secCode`；仍须原文证明“本次发行”及A股角色，核对同一法人、H股原代码、交易所、公告ID、组织ID和URL。其他文件或有明确六位代码的锚点不能借此绕过核验，目录代码错误必须拒绝。目录证据不冒充PDF原文。
- 已核验事件追加到现有链；旧事件、原文件及来源标签不重写。复验须覆盖新依据的正向和错误代码/角色/目录拒绝；接入后比较原范围财务及估值，保留新增公司的真实财务缺项。参考接通不等于财务齐全、经济适配或估值准确。

## 显式参考政策的批量验收

`tools.check_native_sqlite --reference-policy <文件>`只转交原生`from-database`已有的行业/国家覆盖，不改变引擎、网页默认或发布器默认路径。文件契约为`explicit-reference-policy-v1`：必需`version`、非空`approval`、非空`evidence`及非空`requests`；每项仅允许`ticker`、`industry_override`、`country_override`三个非空字符串。证券须存在于本次SQLite且不重复，禁止在该文件夹带财务覆盖、预测或无风险率；无风险率仍由既有独立参数明确指定。

政策审批和证据是调用方声明，不由工具认证经济合理性；政策原文件放workspace并保留。逐公司结果记录实际请求、审批、证据及政策内容哈希，仍保存默认兼容诊断；`admission`及其计数是**默认路径**，`http_status`是**实际显式请求**，不得混为默认参考已闭合。参考代理可以解除参考阻断，不能解除财务阻断；未知行业/国家仍由原生API拒绝。未列入政策的证券沿用原请求。原子发布命令不自动携带此文件；若以后要默认批量映射，需另行批准规则及政策血缘。

## 接入变更验收

1. 先记录源定义、目标实际消费、单位、期间、范围及缺项处理，查全部调用方和本spec，再修改实现；涉及新契约须获明确授权。
2. 完整季度与直接累计路径在相同组成下应等价，按源精度/浮点误差比较；对Q1/H1/9M/年末及跨年验证。删除Q1但保留完整累计三项应可算；删除任一累计项、冲突、单位/身份/范围不符应拒绝或保留缺项。
3. 研发年度队列、最新余额、行情/市值、参考及默认政策分别比较。真实样本经TDX→主库→SQLite→原生API复验，解释所有数值变化；全范围只盘点输入，不为验收运行全市场DCF。
4. 候选经原生API验收、版本一致及只读完整性检查后原子发布；失败回滚。记录主库/SQLite哈希、范围、资源和耗时，验收后删除可重建临时副本。完成度统一写[项目状态](../implementation-status.md)。

## 人民币WACC的显式输入契约

AlphaLake经已有`CompanyValuationInput.methodology_choices.reference_capital_inputs`向原完整`POST /api/valuation`交付显式参考输入，`cost_of_capital_approach=reference_snapshot`。不是向原数据库网页新增JSON表单，不更新引擎算法。方法、源值和适用条件统一见[人民币口径](native-cny-policy.md#人民币输入契约)。

利率、ERP、税率、资本权重用小数比例；Beta无量纲。`risk_free_rate`为指定CNY口径；`mature_market_erp`与已经按政策加权的`country_risk_contribution`分别交付，后者不能再放入成熟ERP。`debt_cost_pretax`是CNY同基准完整利率，`debt_cost_basis`说明公司证据或参考代理；不将US行业完整利率改标签后复用。`market_equity`/`estimated_debt`以与财务相同的百万元人民币计，`capital_structure_basis=market_equity_estimated_debt`，`debt_weight=D/(D+E)`须匹配金额。`tax_shield_rate`须与所选公司边际税率政策及Macro的对应假设一致。

Macro中的CNY无风险率、成熟ERP/CRP和预测假设须同步解释；仅填写参考WACC而继续继承旧终值增长不属于完整人民币条件政策。`cost_of_capital_stable_override`及`stable_growth_rate`分别明示稳态，`roic_stable_override`未填仍按原引擎采用实际终值WACC，既有亏损税盾调整保留。原公司预测、金融调整、研发和股权桥不随接通WACC改写；输入与结果差异逐项记录，运行后回填的资本成本不是新增来源事实。

typed输入不携带每个参考的原始归档定位，AlphaLake须在请求旁保存源版本、定位、哈希、观察日、截止、字段依据及政策标签；不能把结构校验当来源核验。报告的参考快照绑定与显式组件证据分别留痕。公司信用/经营暴露未知时可以用明确命名的条件情景，不冒充已审核公司市场WACC；当前自动选择及网页默认不因接口可表达而获得批准。
