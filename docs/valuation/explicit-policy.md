# 显式估值政策清单

本文统一WACC及经营输入的选择、交付和验收顺序。AlphaLake提供事实、参考及显式假设，复用现有完整`POST /api/valuation`，不增加接口或修改页面。字段路径相对于请求中的`inputs`；具体单位、期间和缺项规则以[数据接入spec](data-contract.md)为准。

**可表达、可复算、经济依据充分、允许自动采用是四件不同的事。** 当前已有条件请求可执行；公司信用、经营持续性和成熟期依据仍有缺口，自动选择与默认替换见[待审批](../../valuation/TODO.md)。本文不构成采用授权。当前数量及数据库范围只在[项目状态](../implementation-status.md)维护。

## 计算前固定输入

每次交付绑定证券范围、财务/股本基期、信息截止、价格观察日、SQLite/参考版本及完整请求；保留同一快照的默认基线。事实、来源参考、已批准代理、继承默认和用户覆盖分别标记，用户显式输入优先。缺项保留原因，不借其他情景或默认值静默补齐。

请求旁保存来源版本、定位、哈希、期间、单位、政策理由及批准范围，沿用已有运行证据格式；这些证据不是新增typed字段。动态请求、结果与收据放`workspace/derived/`，不写进源代码或标准财务事实。

## 折现与融资选择

选择`methodology_choices.cost_of_capital_approach=reference_snapshot`时，下表组件位于`methodology_choices.reference_capital_inputs`。Macro中的币种、无风险率、风险和边际税率假设须同步解释，不能只换初始WACC、保留另一币种的终值参数。

| 输入字段 | 来源与显式选择 | 验收边界 |
|---|---|---|
| `risk_free_rate` | CNY同币种长期利率；国债扣所选主权利差的估计，与直接国债代理分别命名 | 留期限、观察日和参考年龄；过期不能改时间戳放行。归档条件分析不称当前市场参考 |
| `beta_u` | 有业务可比性依据的去杠杆Beta；保留US/Global等实际地域 | 人民币不强制China样本；资本倍率地域不继承Beta地域，不能自动切换 |
| `mature_market_erp`、`country_risk_contribution` | 成熟ERP与已按显式暴露政策加权的国家风险分别供给 | 不重复叠加总ERP中的CRP；注册/上市地不证明经营暴露，λ=1仅为条件代理 |
| `debt_cost_pretax`、`debt_cost_basis` | 同币种完整债务成本；公司债券/评级证据优先，合成估计次之，不足则命名信用情景 | `analyst_credit_reference`可标参考情景，不证明真实评级。US完整债务利率不能重标为CNY；信用分母须正、同期间同范围 |
| `capital_structure_basis`、`market_equity`、`estimated_debt`、`debt_weight` | 当前路径选`market_equity_estimated_debt`，权益/债务金额以百万元人民币供给 | `debt_weight=D/(D+E)`匹配金额；A股价格×总股本及账面债务仍是批准代理，不称完整市场价值 |
| `tax_shield_rate` | 对应所选边际抵税率；会计有效税率另用于经营预测 | 国家税率不证明公司优惠期限或足额抵税；保留既有亏损税盾计算，不改变算法 |

已入表租赁债务不再加一次付款PV，已含租赁利息不重复加入分母。报告EBIT与研发调整后EBIT分别保存，不能在适配层自动更换信用分子。报表信用代理尚是提案；缺/零/负利息和官方区间缝隙保留，不填AAA或插值。具体原始依据及选择顺序见[人民币政策](native-cny-policy.md#数据选择方案待审批)和[组成审计](native-cny-policy.md#利润利息与租赁组成审计)。

## 经营与稳态选择

下表字段位于`valuation_assumptions`。率用小数比例，资本倍率无量纲；历史收入、利润和资本只是选择依据，预测请求不是新事实。

| 输入字段 | 必须明示的选择 | 联动验收 |
|---|---|---|
| `revenue_growth_next_year`、`revenue_growth_years_2_5`、`high_growth_years`、`projection_years` | 同窗收入观测、未来起点、持续期及收敛期限 | 看预测收入绝对规模；一年高增长窗口仍可能有十年渐变，不把最近同比称长期预测事实 |
| `operating_margin_next_year`、`target_operating_margin`、`margin_convergence_year` | 报表→研发/租赁调整桥、未来利润率目标与过渡 | 历史/行业利润率按相同范围和税前后口径比较；正利润不证明持续性，亏损不等于无估值方法 |
| `sales_to_capital_high`、`sales_to_capital_stable` | 公司存量或明确地域的行业资本代理，用于预测新增投入 | 分开存量/新增效率；再投资已是合计净投入，不另加研发或营运资本。资本释放需可回收依据；隐含回报不等于项目IRR |
| `override_reinvestment_lag`、`reinvestment_lag_years` | 是否覆盖投入到收入的时间差 | 不把一年当统一经济规律；沿用现有路径并解释建设/周转假设 |
| `effective_tax_rate_override_years_1_5`、`override_tax_convergence` | 与`macro_inputs`有效/边际税率对应的经营税路径 | 字段名不能代替实际时序：高增长窗口影响税率过渡。分别看税率、NOL、税盾和税后利润，不称纯增长变化 |
| `cost_of_capital_stable_override`、`stable_growth_rate`、`roic_stable_override` | 成熟风险/融资、同币种增长与新增资本回报 | 检查实际终值WACC大于g、ROIC有限且正；`roic_stable_override`留空按现有引擎使用实际终值WACC，是无超额回报情景，不是公司必然状态 |

已有`annual_forecast`可逐年交付`growth/margin/tax`，长度必须覆盖`projection_years`，覆盖两阶段增长/利润率/税率路径，最后一年利润率和税率延续至终值；已有`annual_sales_to_capital`逐年覆盖两阶段倍率，要求全程有限且正。两者不覆盖终值的`g/ROIC`再投资规则。显式零值按其实际语义处理，不当作“未填写”；基期收入为零的商业化路径仍属[引擎待审批](../../valuation/TODO.md)。

研发寿命/队列及租赁调整继续由`adjustment_inputs`交付，现金、投资、债务、少数股权等保留原股权桥。更换利润率或资本代理不能顺便覆盖这些输入。缺研发历史不自动补零、缩短经济寿命或禁用调整；不为此次清单扩采早年。

原始方法见[增长、可持续利润率与再投资](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/littlebook/growthvaluedrivers.htm)、[增长与资本回报](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/growth.htm)、[终值超额回报](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/termvalueexreturns.htm)。具体本地选择及未闭合依据见[经营输入审阅](native-assumption-selection.md#当前经营输入联动审阅2026-10-02)，不能把2%、一年、十年、样本30或五年研发寿命称为教材统一要求。

## 现有方案与采用状态

| 方案 | 用途 | 状态 |
|---|---|---|
| 原生默认基线 | 同快照下保留原模型输入，供变化归因 | 继续可用；默认数值没有因此获得公司经济认证 |
| 已验证人民币归档参考条件 | 保留当前US业务Beta及权益/债务代理，显式CNY利率、风险分列、命名信用与稳态 | 完整API已验证；参考年龄、BBB及全中国暴露限制保留，不自动发布为市场WACC |
| 已有v2经营条件 | 最近可比同比、保持正的调整后利润率、Global行业资本代理、显式成熟期条件 | 现有选择器可生成并计算；保留亏损拒绝、资本释放和终值切换诊断，不自动替换默认 |
| 报表信用代理及新默认政策 | 从类别证据选择信用、增长持续期、利润率目标、资本效率和稳态 | 尚待审批；不是新API枚举，不在适配层隐式实施 |

## 交付与下一轮顺序

1. 按本清单选定已有显式条件，固定样本、参考截止、完整基线及停止条件；依据不足的项目标为未闭合，保留失败和缺项分母，不逐个股按价格调参。
2. 复用现有选择器、完整API及审阅器，分别比较收入/利润、净再投资/FCFF、逐年税率/WACC、终值和股权桥；单因素归因必须确认其余实际输入相同。已有负向检查继续覆盖缺/零倍率、金额/权重冲突及FCFF篡改。
3. 如需采用新规则，提交可审核的具体选择及跨公司影响到[TODO](../../valuation/TODO.md)；获批后才改自动选择或默认，并按[引擎台账](engine-change-ledger.md)记录。条件结果和默认发布分别处理，参考刷新仍手工进行。

现阶段优先复用已交付证据，集中解释规模、持续性、资本释放与成熟期投入的经济限制；不新增政策引擎、不扩大样本或全市场重算、不因信用采用待审批停止既有数据链路工作。
