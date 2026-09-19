# 016：多市场身份与 WACC 数据表设计

日期：2026-09-09

状态：部分结构已实现。迁移 027 建立发布版本、归档关联及四类参考观测；后续已接入 CN/HK/US 国家风险的限定发布链（见[同步说明](../country-risk-sync.md)），全球行业 Beta 与人民币国债收益率也已接入（见[说明](../beta-yield-sync.md)），[WACC 固定版本选择器](../wacc-valuation-bridge.md)已实现；迁移 028 及[合成评级利差链](../credit-spread-sync.md)扩展大型非金融利差档位，[港元汇率](../fx-sync.md)已接入；迁移030及[市场WACC](../market-wacc.md)实现证据限定的公司、类别、listing与股本/报价链；迁移031补充[安克两次H股融资事件](../wacc-gap-review-20260909.md)，金额为发行人估计净额，日期明确区分已披露/预计挂牌日，不代表结算日。

本文原 `ref` 名称已由迁移043替换为 `core`，风险参考由044拆分，见[当前迁移说明](../core-risk-migration.md)。以下字段名按新结构表述；历史迁移名称保持原样。

## 范围与现有基础

目标是让人民币估值先具备可追溯的 WACC 输入，同时允许后续接入港股及其他市场。复用 DuckDB、不可变归档、采集运行、诊断、分类体系和现有估值运行快照；不增加数据库或通用数据平台。

TDX 仍是当前公司财务主要结构化来源，CNINFO 提供公告与核验、显式补充。达摩达兰的风险和行业统计进入独立参考层，不回写公司财务事实。政策、参考估计和公司披露分别留痕。

当前 `core.company`、`core.exchange`、交易日历仅有结构；`core.instrument` 混合证券和上市地点属性。`fundamental.fact` 按证券组织，股本未明确公司总量与股份类别范围，旧日线主键不能保留同来源同日多次修订；迁移 029 已新增[大陆行情兼容观测层](../valuation-quotes.md)保留后续采集版本，后续迁移030已对安克/茅台建立证据限定的 listing 与股份类别关系，但不恢复升级前历史，也不建立全市场历史身份。因此当前能力不能宣称已经支持多市场公司级估值或市场数据双时态。

本文字段表是迁移与接口的设计契约，不是可执行 DDL。除明确标注可空或条件字段外，字段必填；所有新增 ID 使用稳定内部 BIGINT 主键，外部代码不作主键。外键、枚举、唯一性和数值约束在实际 DDL 落地；时间区间重叠、跨表语义、批次完整性由发布事务校验并留诊断。

## 1. 公司、证券和上市记录

```text
company ──< instrument ──< listing ──< 行情观测
   │             └── 股份类别股本
   └── 合并财务／公司总股本
```

公司是发行人身份，不自动等于任意合并报告范围。证券代表具有确定经济权利的工具；上市记录代表它在某场所、某币种下的报价身份。

### 1.1 复用与扩展

| 表 | 目标字段及约束 |
| --- | --- |
| `core.company` | 复用 `company_id`、名称；现有 `country_code` 明确为注册国家，不表达经营暴露。无证据不填公司映射。 |
| `core.instrument` | 复用 `instrument_id`、`instrument_type`、`company_id`；股份类别由独立 instrument 表达。旧 `exchange_mic`、`currency` 在迁移过渡期保留，最终由 listing 提供报价属性。 |
| `core.listing`（新增） | `listing_id`、`instrument_id`、`exchange_mic`、`trading_currency`、`valid_from`、可空 `valid_to`、`artifact_id`、`recorded_at`。有效区间为半开区间；明确交易场所和报价币种。 |
| `core.listing_identifier`（新增） | `listing_identifier_id`、`listing_id`、`provider`、`identifier_type`、`identifier_value`、`market_namespace`、`valid_from`、可空 `valid_to`、`artifact_id`、`recorded_at`。唯一键为来源、类型、命名空间、值、起始日；同一外部标识有效区间不得重叠。 |
| `core.instrument_identifier` | 继续承载证券级身份。旧 TDX 标识解析保留；新增 listing 映射不能悄然改变既有 instrument ID。 |

交易代码是字符串，保留前导零。日期或公司身份未知时，留在待解析证据中，不编造有效起点、不根据名称合并公司。公司—证券关系更正必须保留归档证据及所影响的旧估值快照；正式历史身份查询需要版本化关联后才能开放。

A/H 股为同公司下不同 instrument；同一证券多地报价不能重复计股。ADR 属独立证券，与基础证券的转换比例及有效期间在实际接入时增加关系表；第一阶段明确拒绝自动聚合 ADR 市值。

### 1.2 股本与财务范围

保留现有 `market.share_capital` 为来源观测。新增标准 `market.share_count_observation`：

| 字段 | 类型／规则 |
| --- | --- |
| `observation_id`, `release_id` | BIGINT；主键及来源版本 |
| `company_id`, `instrument_id` | 条件可空；依据 `scope` 恰好一个有值 |
| `scope` | `company_total` / `share_class` |
| `share_basis` | `issued` / `treasury` / `outstanding` / `free_float`，分别记录 |
| `effective_date` | DATE；生效日，不等于披露日 |
| `value` | DECIMAL(38,10)，非负；保留源精度，不从 float32 恢复整数精度 |
| `source_locator` | 原记录或单元格定位 |

唯一键按 company_total 和 share_class 分别校验 `(release_id, subject_id, share_basis, effective_date)`；不能依赖包含 NULL 的复合唯一键完成该约束。一个发布版本可包含多条原始证据，但同一标准键冲突不得任取最后一条。

市值使用类别 outstanding 股数和对应未复权价格；公司总股数不得复制到各类别。双重上市的同类股份只计算一次。不同类别先分别计价，再按选定汇率汇总。

财务最终查询粒度为报告主体、报告范围、会计准则、报告币种、起止期间、报告版本、项目。现有 `fundamental.fact` 不在本轮改主键；先通过可信公司映射提供查询，重复证券不得导致同一合并报告重复求和。跨准则和币种的字段未经语义审核不得合并。新市场财务接入前另行迁移报告主体与期间模型。

## 2. 发布版本与来源定位

新增 `meta.dataset_release`，复用 `meta.artifact`、`meta.ingest_run` 和 `meta.validation_result`。

| 字段 | 类型／含义 |
| --- | --- |
| `release_id` | BIGINT PK |
| `source`, `dataset` | VARCHAR，受支持数据集标识 |
| `source_version` | VARCHAR，可空；来源原有版本标签，不用文件名推断发布时间 |
| `content_key` | VARCHAR；排序后的输入归档哈希及解析／标准化版本生成的内容签名 |
| `source_published_at` | TIMESTAMPTZ，可空 |
| `publication_precision` | `timestamp` / `date` / `unknown` |
| `available_at` | TIMESTAMPTZ；本系统采用的保守可用时间 |
| `availability_basis` | `published_timestamp` / `published_date_boundary` / `first_seen` |
| `first_seen_at`, `recorded_at` | TIMESTAMPTZ；首次取得该内容、本次标准发布入库时间 |
| `parser_version`, `normalization_version` | VARCHAR；固定解释规则 |
| `ingest_run_id` | BIGINT |
| `supersedes_release_id` | BIGINT，可空；仅指明同一发布范围的更正关系 |

唯一键 `(source, dataset, content_key)`。新增 `meta.dataset_release_artifact(release_id, artifact_id, role)`，三列联合主键，使一个版本能关联数据文件、发布说明和时间证据。观测行携带 `artifact_id` 与 `source_locator`，并要求该归档属于所引版本；单文件数据集也沿用该关联。

仅通过校验的版本进入发布表，失败批次保留归档和运行诊断；观测、版本和检查点原子发布。重复下载相同内容不产生新版本。新版缺少旧行，只有来源声明完整替换且完整性校验通过时才能视为撤回，否则拒绝发布为完整快照。

数据基准日保存在观测中，不把一份行业文件的发布年份当作所有样本财报年份。一个 release 只承载具有共同可用时间的发布单元；如文件内记录具有独立公告时间，应拆发布单元或在该来源适配前明确行级时间模型。

## 3. 标准市场与参考观测

以下表共有：`observation_id BIGINT PK`、`release_id BIGINT`、`artifact_id BIGINT`、`source_locator VARCHAR`。标准值使用 DECIMAL(38,12)；原始数值文本保留为 `raw_value VARCHAR`，原始单位保留为 `raw_unit VARCHAR`。计算可使用浮点，但不得声称获得高于源值的精度。

### 3.1 利率与汇率

| 表 | 专属字段 | 版本内唯一键 |
| --- | --- | --- |
| `market.yield_curve_point` | `curve_code VARCHAR`、`observation_date DATE`、`currency VARCHAR`、`tenor_months INTEGER`、`rate_type VARCHAR`、`compounding VARCHAR`、`day_count VARCHAR`、`value DECIMAL` | release + curve + 日期 + 期限 + rate_type + compounding + day_count |
| `market.fx_rate` | `base_currency VARCHAR`、`quote_currency VARCHAR`、`observed_at TIMESTAMPTZ`、`time_precision VARCHAR`、`fixing_code VARCHAR`、`rate_type VARCHAR`、`value DECIMAL` | release + 币种对 + observed_at + fixing_code + rate_type |

利率以小数存储，允许负利率；第一阶段只接明确月数期限的曲线点。曲线标识绑定提供者和方法定义，不能把国债到期收益率、即期收益率或信用债收益率混用。复利和日计数未知时使用显式 `unknown`，需要该口径的转换不得继续计算。

汇率表示一单位 base 等于多少 quote，值必须大于零、币种不得相同。中间价、收盘价、即期买卖价分别保存。时间只有日期时保留精度及来源时区，规范化时间仅是日期载体，不声称存在精确成交时间。倒数或交叉汇率属于派生输入，记录原观测及公式，不伪装成源报价。

币种代码、国家代码、市场代码使用不同字段和校验目录；国家不决定估值币种，交易所默认币种不替代 listing 报价币种。

### 3.2 国家风险与行业统计

| 表 | 专属字段 | 版本内唯一键 |
| --- | --- | --- |
| `reference.country_risk` | `subject_kind`（country/market_group）、`subject_code`、`observation_date`、`metric_code`、`method_code`、`value`、`value_status` | release + 对象类型及代码 + 日期 + metric + method |
| `reference.industry_stat` | `industry_node_id BIGINT`、`sample_region VARCHAR`、`observation_date DATE`、`metric_code`、`method_code`、`statistic_code`、可空 `sample_count INTEGER`、`value`、`value_status` | release + 行业节点 + 样本区域 + 日期 + metric + method + statistic |

对象与方法代码使用白名单；成熟市场 ERP 使用 market_group 对象，不虚构国家代码。`value_status` 为 `reported` / `missing` / `not_applicable`；仅 reported 允许且必须有数值，其余 value 为 NULL。原文件不存在的项目不凭空生成一行，覆盖率查询通过需求清单识别缺项。

第一批指标目录随代码版本维护，暂不增加可动态编辑的指标配置表：

| metric_code | 单位与含义 |
| --- | --- |
| `mature_market_erp` | 小数比例；必须锁定 ERP 方法及与无风险基准的配套口径 |
| `country_risk_premium` | 小数比例；评级法与 CDS 法不同 method |
| `total_equity_risk_premium` | 小数比例；不得再次加 CRP |
| `sovereign_default_spread` | 小数比例；不得当作股权 CRP |
| `beta_unlevered` | 无量纲；与现金调整版本分开 |
| `beta_unlevered_cash_adjusted` | 无量纲；明确方法版本 |
| `debt_equity_ratio` | 小数比例；注明账面／市场口径 |
| `effective_tax_rate` | 小数比例；不直接替代公司债务税盾税率 |

指标目录还约束允许的表、方法、单位和适用校验；Beta 不施加统一 0–1 约束，税率异常不静默截断。发行人财务值仍只走 fundamental 链路。

复用 `classification.taxonomy/node`，达摩达兰采用独立、带版本的 taxonomy_code，例如 `damodaran_industry_2026`。当前 node 表无历史名称版本，不能覆盖旧节点后仍声称历史定义未变。跨年度行业对应需显式映射；公司行业选择属于估值政策，不由模糊名称匹配自动确认。

评级—信用利差表下一批按实际源结构设计，至少必须区分评级体系、公司样本类型、覆盖率区间与边界、有效版本；不把国内评级直接映射为海外评级。

## 4. 历史选择与缺失规则

查询显式传入 `valuation_at`、`information_cutoff`、可选 `recorded_cutoff` 和数据选择政策。先限定来源、方法、口径与观测日期，再限定 available_at；严格重放系统当时所知时，还限定 recorded_at。只按最大 release_id 或入库时间取最新禁止用于估值。

修订按显式替代关系和发布时间处理；迟到的旧发布不成为新数据。不同来源或方法并存，由政策选择，不能以一个全局来源优先级跨指标套用。替代关系不得成环，不同发布范围不得互相撤销。历史查询必须先选择发布快照再取行，避免已撤回项目从旧快照复活。

日期精度时间按该来源发布时区的下一日零点保守处理，不能对全球来源统一套 UTC 16:00。无法核实发布时间则用首次发现时间；后续找到时间证据时生成新解释版本，旧运行不变化。

市场日期是交易所本地日期，时间戳统一保存 UTC；跨市场市值汇总必须记录各自实际收盘时间及陈旧程度。允许回退到前一交易日的上限由政策显式规定，禁止使用截止时点之后的收盘价或汇率。

## 5. WACC 政策和运行快照

复用现有 valuation 不可变运行文件，不新增一组重复的估值 SQL 表。快照契约扩展：

- 主体：company、估值范围（集团／经营部分）、估值币种。
- 时点：valuation_at、information_cutoff、recorded_cutoff（可空）、实际各市场观测时间。
- 政策：版本与内容哈希；行业组合、地域暴露、资本结构、税盾税率、缺失替代和陈旧容忍规则。
- 输入：指标名、观测表及 ID、release 内容签名、artifact 哈希、实际值、单位、来源或假设类型、选择理由。
- 结果：Rf、Beta 调整、ERP／CRP 分量、Ke、Kd、E/D 权重、WACC、引擎版本和公式。

本地数据库 ID 仅用于查询；快照同时固定内容签名、原始归档哈希及完整取值，以便跨数据库重建。主观附加风险单列，不能标为外部观测。公司 `country_code` 不自动生成地域权重；未披露的地域或业务权重明确为假设。

WACC 不是 company 上的单值属性。茅台财务公司与酒业的估值范围分开，存款负债不能自动进入酒业 D；安克可转债的债务与权益选择权处理和股权桥接必须一致，不能重复扣减。债务账面值替代市场值时标明近似和依据。

## 6. 小步迁移与验收

按依赖顺序实施，每步单独迁移和验收。

1. 发布版本及参考观测：新增 release、归档关联、利率、汇率、国家风险、行业统计。先接实际文件并锁定来源字段含义，暂不改公司财务查询。
2. 公司与上市身份、标准股本：补可信映射后引入 listing 和类别股本；保留旧 A 股接口。不能根据当前快照伪造全历史上市区间。
3. 行情版本：新增按 listing 及不可变发布版本组织的观测；旧 OHLCV 保留兼容投影。旧行只在有证据时回填 listing；没有保留的历史修订明确不可恢复。
4. 估值选择与 WACC：先茅台、再安克，扩展现有输入快照和计算路径。历史财务 ASOF 不因本设计自动升级成系统双时态。

正式迁移前至少锁定以下验收情形：

| 情形 | 必须成立 |
| --- | --- |
| A/H 股 | 同公司不同证券；类别股本分别计价；合并财务只计一次 |
| 同股多地报价 | 一份股本不因多个 listing 重复计入市值 |
| 代码复用／重叠映射 | 按业务时间解析；歧义拒绝，不取任意匹配 |
| 相同文件重放 | 不增加版本、观测或推进无变化检查点 |
| 新版缺行、批次失败 | 未经完整性确认不撤销旧值；失败不发布可信快照 |
| 更正与迟到旧文件 | 历史截止前后取正确版本；旧估值快照不变化 |
| 日期精度及跨时区 | 边界前不可见、边界后可见；不引用未来收盘价 |
| 百分比、负利率、缺失、真实零 | 分别保留；无隐式零填充和 100 倍误差 |
| ERP／CRP 和 Beta 口径 | 不重复加溢价、不混用现金调整、单位与样本地域 |
| 汇率方向／跨币种 | 固定报价定义；中间价与收盘价不悄然替换 |
| 原始归档或解析版本变化 | 生成可追溯新解释；同内容重建结果一致 |

## 参考入口

- [现有参考身份结构](../../internal/store/duckdb/schema.sql)、[行情结构](../../internal/store/duckdb/schema.sql)、[标准事实](../../internal/store/duckdb/schema.sql)。
- [达摩达兰数据目录](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datacurrent.html)与[时间、定义及使用说明](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datahistory.html)。
- [中债曲线](https://yield.chinabond.com.cn/)、[中国货币网汇率](https://www.chinamoney.com.cn/chinese/bkccpr/index.html?tab=2)。正式接入另行核验字段、下载方式和数据使用条件。

## 迁移 027 的实现边界

- 已建立六张表及行内约束：内容签名唯一性、时间依据组合、真实零／缺失、汇率正值与币种方向、指标白名单、观测键唯一性。同 schema 的发布—运行、归档关联和前序版本使用外键。
- 尚未建立跨 schema 的观测—归档关联、行业节点外键；国家风险、Beta 和国债曲线写入函数共用归档身份校验与事务关联；Beta 在同一事务内核对并创建版本化行业节点，其他写入 API 落地前仍须补足相应校验。SQL 结构不阻止直接修改已发布行，也不独立证明发布完整性、来源白名单、前序版本同范围、原始单位转换或日期时区边界；仅完成上述校验的限定写入路径可发布，其他表尚不能直接对外提供可信发布接口。
- 货币字段当前检查三位大写格式，不代表代码已通过币种目录校验；方法、统计口径和时区当前检查非空，实际来源接入必须审核白名单与语义。
- 对设计的两个补充：收益率唯一键包含币种；汇率增加 `source_timezone`，保留日期精度报价的原时区。
- 首批曲线仅支持到期和即期收益率；远期利率需要额外起始期限，暂不接受 `forward`。
- 升级测试从含既有数据的 schema 26 执行迁移，验证约束拒绝、版本并存、重放和重开持久化。外部数据源解析、ASOF 和生产存量库升级未在该测试中验收。

## 来源证券行业观察（迁移036）

`reference.security_industry`保存来源交易所ticker、行业节点、原始行载荷和release/artifact/单元格血缘；唯一键分别为`(release_id, exchange_ticker)`与`(release_id, source_locator)`。这是来源观察，不是标准证券成员关系，避免在不知道来源生效日期或尚未解析身份时填入`instrument_id`。来源未标版本日期时，参考发布保留`source_version=NULL`；各已知日期的源解析器继续强制自己的日期契约。

首批仅解析官方名单的SHSE/SZSE范围，其他市场仍保留原始证据；不把BSE缩写猜为北交所，不从Country推断上市地或估值风险暴露。本地身份/标准分类物化须独立记录解析结果、时点依据及完成边界，来源名单发布不能代替该阶段验收。
