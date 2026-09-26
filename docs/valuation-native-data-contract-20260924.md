# 原生估值字段契约与数据阻断

当前允许限定样本使用[显式账面代理](valuation-book-proxies.md)。下表继续描述原报表事实列的语义和缺项；估值代理不改写这些列，不将条件计算成功称为完整原SQL事实供给。

当前更新：[2026-09-26重新接入](valuation-reconnect-20260926.md)后，安克债务、历史研发与市值代理已供给；现金等价物、长期股权投资以已知分量接入，仍有两项完整目标范围未闭合。以下表格保留此前逐项审核背景，六类未映射不再是当前实现状态；历史窗口19→8只是覆盖统计，不是估值必需项。

范围：三家公司（300866、002032、600519），2026H1、截止2026-09-22；现有SQLite只读检查，非全市场。原消费者依据导入提交`0b29f42`的`us_cn_hk_mapping.py`、`api/database.py`及`RawFinancials`。逐公司覆盖见[机器收据](acceptance/native-sqlite-20260924.json)。

## 财务宽表：全部17列

共同契约：金额为百万元报表币种，股数为百万股；FY为全年累计，FQ为独立单季。累计差分仅限同年且各项完整，存量不差分；TTM须完整窗口。标准值及源精度保留在证据中，不按模型需求补零。下表“已映射”不等于完整历史或逐公司金额认证。

| 目标字段 | 原始CIQ列/消费者含义 | TDX标准来源或候选 | 当前结论与需要补充的证据 |
| --- | --- | --- | --- |
| revenues | Total Revenue；模型必需 | revenue_cumulative | 已映射累计/单季；三家公司历史窗口均不满10年 |
| ebit | EBIT；模型必需 | reported_ebit | 已补标准映射与SQLite导出；来源报告值不能冒充达摩达兰调整后经营收益，见[组成核验](tdx-supplementary-fields-20260925.md) |
| ebitda | EBITDA | reported_ebitda | 已补标准映射与导出；供应商指标保留，租赁及经营调整另核 |
| net_income | Net Income | 当前使用net_income_parent_ytd | 待核目标归母/合并含义；不得仅凭短名称认证 |
| interest_expense | Interest Expense；正支出 | interest_expense | 已映射；租赁利息是否包含随报告期留证，不用缺失表示无利息 |
| capex | Capital Expenditure；原导入转正支出 | capital_expenditure_cash | 已映射购建现金流；不是净再投资或全部资本形成，处置/非现金购建另列缺口 |
| d_a | Depreciation & Amortization | depreciation_depletion、intangible_amortization、deferred_expense_amortization、right_of_use_depreciation等 | 未闭合；分量合并/拆分随期间变化，不能无条件求和 |
| earnings_before_tax | **EBT Excl Unusual Items** | profit_before_tax仅为利润总额 | 已确认不是无条件等同；本轮撤销错误直映，特殊项目调整未闭合 |
| total_tax_expense | Income Tax Expense | income_tax_expense | 已映射；与剔除特殊项EBT的配对语义须另外核对 |
| operating_lease_expense | Operating Lease Payments | TDX租赁负债/折旧不是租金支付 | 缺同义字段及到期承诺；原后端缺值会关闭租赁调整，不能据此声称租赁无影响 |
| r_and_d_expense | R&D Exp. | research_and_development_expense | 已映射并移除2025年审核起点误限；安克2020—2024队列已补齐，其他缺历史不补0 |
| cash_and_marketable_securities | Total Cash & ST Investments | cash_and_cash_equivalents、货币资金、交易性金融资产等候选 | 已接现金等价物及组成引用，标记partial_target_scope；短期投资的流动性、受限、期限与重叠仍待分类 |
| cross_holdings | Long-term Investments | long_term_equity_investments及其他长期投资 | 已接长期股权投资及其他组成引用，标记partial_target_scope；不冒称全部长期投资 |
| bv_debt | Total Debt | 短长借款、债券、一年内到期、租赁等 | 已接五项账面组成合计；其他应付款融资性质、到期项范围与重复租赁调整仍须审阅 |
| bv_equity | Total Equity | 当前使用equity_parent；另有total_equity | 待核原字段少数权益边界；不凭名称直接改成另一数值 |
| shares_outstanding | Total Shares Out. on Filing Date | total_shares | 已映射财报日股数；不冒充当前流通股或稀释股数 |
| minority_interests | Minority Interest | noncontrolling_interests | 已映射账面数；不冒充市场价值 |

2026-09-25三家公司新快照已实际导出；当时十年/八季窗口收入/EBIT空格为安克19格、茅台和苏泊尔各22格，旧门槛将其全部视为必需并阻断；这一门槛现已纠正，收据仅保留历史行为。详见[收据](acceptance/tdx-supplementary-sqlite-20260925.json)。

2026-09-25导出器已增至11项直接映射、6项目标定义待补；EBIT/EBITDA已有来源报告值，不能再称TDX没有。这两项的估值调整适用性仍须与来源映射分开审核。旧快照未重写：原219个available中有21个`earnings_before_tax`单元格现在不能继续视为目标语义合格；不得拿旧收据宣称当前目标字段已审核。该修复以“即使有利润总额也不填剔除特殊项目EBT”的负向测试锁定。

## companies：全部28列

公司身份与财务均在既有截止内取数；表内NULL保留，不从演示数据或他家公司补值。

| 列 | 目标单位/时点 | 当前来源与缺口 |
| --- | --- | --- |
| ticker | 交易所:字符串代码 | 业务时点证券身份；已有 |
| company_name | 名称 | 主数据；已有 |
| company_type | 公司类型 | 已导出Public Company；不等于行业/模型准入 |
| exchange_code | 目标交易所编码 | 已映射SHSE/SZSE/BJSE |
| primary_exchange | 主要上市交易所 | 当前按所选A股身份；跨市场主上市判断未认证 |
| secondary_exchanges | 次要上市地 | 未供给；不能据空值断言无其他上市地 |
| region | 市场编码 | 当前CN；原国家风险查找使用名称，需显式国家映射，不能回落到美国 |
| filing_currency | ISO报表币种 | 当前CNY |
| listing_currency | ISO交易币种 | 当前A股CNY |
| fx_listing_to_reporting | 报表币/交易币 | 同币种1；跨币种另接参考来源 |
| fx_rate_source | 汇率依据 | 当前same currency |
| effective_tax_rate | 小数比率，最近完整年度 | 已接所得税费用/利润总额；缺项、分母≤0或比例不在[0,1]时留空并诊断，不冒充未来正常化税率 |
| stock_price_listing | 元/股，价格时点 | 已有合格行情时供给报告期附近未复权收盘价；安克已接入，价格日单独留证 |
| mv_equity_listing | 百万元，价格/股本配对时点 | 已有价格及股数时计算报告期市值代理；已知多股类拒绝，不冒称当前公司市值 |
| actual_rating_fc | 长期外币发行人评级 | 未供给，TDX财务非该评级来源 |
| actual_rating_lc | 长期本币发行人评级 | 同上；合成评级为模型估计，非实际评级 |
| options_outstanding | 百万份，期末 | 未供给，可能需激励附注/公司公告 |
| options_avg_strike | 交易币/份 | 未供给；不能默认0后声称无稀释 |
| period_date_annual | 最近完整财年末 | 当前2025-12-31；不取服务器年份 |
| period_date_quarterly | 最新季度末 | 当前2026-06-30 |
| lease_commitment_yr1 | 百万元，未来第1年 | 未供给，TDX负债余额不等于承诺支付 |
| lease_commitment_yr2 | 百万元，未来第2年 | 同上 |
| lease_commitment_yr3 | 百万元，未来第3年 | 同上 |
| lease_commitment_yr4 | 百万元，未来第4年 | 同上 |
| lease_commitment_yr5 | 百万元，未来第5年 | 同上 |
| lease_commitment_beyond | 百万元，之后期间 | 未供给；原列标题与模型beyond含义也须核对，不能自行外推 |
| geographic_segments_json | 收入地域金额/占比及范围 | 未供给，地区风险不能凭上市地替代所有经营地 |
| data_as_of | 信息截止日期 | 已供给；不是首次取得或当前行情时间 |

## 原模型还需要的参考与参数

| 输入 | 来源/处理 | 边界 |
| --- | --- | --- |
| 行业及行业统计 | 原industry_mapper、DamodaranStore或后端等价适配 | 匹配失败不可选择列表第一项作为公司行业；自动映射须留依据 |
| 国家ERP、法定税率、评级利差 | 已有达摩达兰参考数据 | 版本、国家编码、币种与业务日期分开留痕 |
| 无风险利率 | 原请求参数和原页面 | 原默认4.25%只是默认假设，不是人民币市场观测；未来自动供给单列 |
| 增长、利润率、资本效率、终值 | 原ValuationAssumptions及原页面 | 本轮不增加历史规则表单，不把行业值写成公司财务 |
| 方法选择、研发寿命、租赁及期权选择 | 原MethodologyChoices与原页面 | 缺事实必须与用户主动选择区分，不能用默认关闭表示不存在 |

## 界面纠偏阶段的阻断与方案（2026-09-24历史记录）

以下为当时快照，不代表当前缺项；当前安克仅余两项资产总额范围，见[重新接入验收](valuation-reconnect-20260926.md)。当时三家公司均不能通过原生输入：每家18行年度/季度中EBIT全空，收入另有11个空单元格，共29个必需输入空单元格。历史表填了行数，不代表取得了对应年度数据。茅台另有已证实金融业务边界；资料补齐也不自动适用普通企业模型。

可选方案：

1. **推荐：批准一个后端“TDX原生财务口径”契约，保持原Web和模型流程。** 针对EBIT、税前特殊项、债务、现金/投资、D&A、租赁逐项固定组合及缺项规则；先安克/苏泊尔做真实对账，缺分类和历史窗口时仍拒绝。明确这是有据的通用口径转换，不声称逐值复制CIQ私有标准化结果。只有所需事实齐全才发布SQL/API。这是在本spec范围内继续推进的方案，但本轮尚未取得足够定义/分量证据批准所有公式。
2. **必要缺口允许独立补充来源。** 租赁承诺、限制性现金、债务拆分、期权等按清单补公告附注，或由用户经原界面显式输入；补充值与TDX分层，不能再称“财务数值全部纯TDX”。扩展此来源范围需要用户审批，未自动实施。历史主表先复用已归档TDX及标准物化条件，不为补界面十年行数无差别抓取公告。
3. **严格对齐原CIQ字段及历史口径。** 取得合法可用的CIQ字段词典、透明度明细或对照数据，核定排除项目和范围；可能需要现有授权/采购。未经授权不购买、不接收费接口。也不能保证仅靠TDX复制其所有附注标准化。

不选用的做法：把营业利润直接当作已认证EBIT、把利润总额当作剔除特殊项目EBT、以现金/债务缺项为0、关闭租赁和期权后声称完整、再增加专用前端。

调研依据：S&P官方明确区分[standardized与as-reported财务数据](https://www.marketplace.spglobal.com/en/datasets/s-p-capital-iq-financials-%2810%29)，并提供[附注及标准化分量透明度](https://www.support.marketplace.spglobal.com/content/dam/spglobal/mi/en/documents/marketplace/newsletters/2024/marketplace_data__solutions_communique_december_2_2024.pdf)。本轮公开检索未取得足够的逐字段排除项目规则，因此不能声称已经复刻。IFRS官方说明[租赁的资产负债确认](https://www.ifrs.org/issued-standards/list-of-standards/ifrs-16-leases/)及[费用变为折旧和利息的影响](https://www.ifrs.org/-/media/project/leases/ifrs/published-documents/ifrs16-effects-analysis.pdf)；这支持“必须核对组成、防止重复调整”，不直接认证每家A股口径。达摩达兰[租赁影响表](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/leaseeffect.html)分别列示租赁调整前后债务和经营利润，也不能据一个负债余额推造缺失支付。

后续已完成用户授权的TDX目录映射与同步及本轮重新接入；只有确认官方字段及现有分量不能满足的补充来源才需审批。不会修改公式或注入代理值强行放行。

## 界面纠偏阶段的历史验收范围（2026-09-24）

- 恢复原侧栏、`RoutedPages`、原请求`{ticker,risk_free_rate}`、原普通会话PATCH；删除`TDXValuation`、政策上传/表单、前端来源分支及后台专用估值旁路。专项CLI/API历史不删除，但不参与本轮原生兼容验收。
- 新增只读`GET /api/database/compatibility/{ticker}`，逐列列出覆盖与必需缺项。当时v2证据快照尚无原生语义认证；即使人为把缺列填成数字也不会放行。它是阻断诊断，不是已完成的自动适配器。
- 前端保留差异仅限明确BUG：已有React/TypeScript类型与未使用变量构建修复、此前行业EBIT增长与收入增长的语义修正、ROIC允许空值的类型修正、本轮错误详情显示及演示TTM修复。没有新增页面或参数。逐文件导入基线与现文件SHA256见机器收据。
- 本轮演示BUG可复现：四个季度却声明年报后两个季度，原LTM要求六个季度，产生500。修复为使用其现有完整年度数据（季度列表空、旋转0、季度日期空），不补造数据，也不将演示结果称为真实公司估值。
- 原PATCH成功后将覆盖字段标记为用户输入，修复修改后仍保留原来源标签的问题；只变内存会话，不写回SQLite。
- 修复导出器`profit_before_tax → earnings_before_tax`错误直映；既有快照不覆盖，保留旧值供审核，不再用于认证原生输入。
- Python相关回归149项通过（不是全套Python）；Go全套测试与CLI构建通过；前端构建通过，仍有既有bundle大小提示。浏览器验证三家真实数据的原请求及明确拒绝，并用年度演示验证原页面导航、PATCH数值变化/用户覆盖来源、敏感性和工作簿导出，零浏览器异常。
- 测试在独立1GiB限额服务中串行运行；后端相关测试的临时目录与程序使用退出清理。真实SQLite哈希保持不变；不生成新的整库副本，不触碰seed。

本阶段已完成界面/契约纠偏与阻断取证，**完整TDX→原生估值仍未完成**。所需审批是是否允许为具体缺口引入独立附注/人工补充或授权对照数据，不是再次申请已授权的普通映射开发。
