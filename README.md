# AlphaLake

AlphaLake 是面向投资研究、本地优先且可复现的金融市场数据基础设施。

估值引擎与应用已纳入 [`valuation/`](valuation/README.md)，源自 [chrisuzy/Investment_Valuation_Agent](https://github.com/chrisuzy/Investment_Valuation_Agent)。感谢原作者 Chirs Yu Zhang 及上游贡献者；原 MIT 许可证、导入版本与 Credits 见 [来源记录](valuation/UPSTREAM.md)。已接通实际数据库到估值 CLI/API 与原生股权桥接，使用方法和适用边界见[融合说明](valuation/docs/alphalake-integration.md)。

它通过多个数据源适配器采集数据，将记录归一化为标准模型，在 DuckDB 中存储分析数据，并保留重建、校验和派生数据集所需的血缘信息。数据源提供稳定文件时，系统将其保存为不可变的原始证据。

当前已完成[普通非金融企业估值闭环验收](valuation/research/method-closure-20260912/README.md)：复用标准数据和共享引擎，逐项对齐经营收益、再投资、WACC、终值及股权桥接；修正缺资本仍生成隐含ROIC的诊断，统一入口新增方法范围与缺口披露。该轮安克、苏泊尔基线保持不变；后续已审核资产的影响见下文。计算和输入衔接可复验，公司预测依据、完整经营资本及市场股权桥接仍有明确缺口；不称完整公允价值或预测准确性认证。[历史预测研究](docs/valuation-accuracy.md)单独保留，失败候选不推广，未来评分等待数据不阻塞当前方法验收。

[公司输入审阅台账](valuation/research/company-inputs-20260917/README.md)之后，已接通[经审核资产的统一估值链路](valuation/research/reviewed-assets-20260917/README.md)：显式绑定公司、期间及证据版本，缺证或过期拒绝；安克联营投资完成真实隔离库验收。默认行业政策不变；[增长—资本诊断与增量重估](valuation/research/automatic-valuation-20260917/README.md)已接入，保留失败和审核过期，不把旧价格当新结果。苏泊尔[镜像原文及受限分量](valuation/research/reviewed-assets-20260917/supor/README.md)已在主库副本完成归档→补充导入→统一估值验收，固定经营假设43.2526→45.7299元；主库未发布；[安克资本证据联合审核](valuation/research/company-inputs-20260917/README.md#安克公司资本效率的审核结论)仍不足以批准公司倍率，保持显式行业代理。

三条主线的交付证据、验证范围及剩余边界汇总于[交付核对](docs/company-input-delivery-20260917.md)。

## 初始范围

- TDX 提供的 A 股日线 OHLCV 和市场参考数据
- TDX 提供的公司行动、股本变动、分类及指数/板块成员关系
- 以 TDX 专业财务数据作为主要结构化基本面来源
- 以 CNINFO 公告作为权威校验与血缘来源
- 以 DuckDB 作为标准分析存储

TDX 协议请求支持[自动换节点重试](docs/tdx-failover.md)：每个独立请求最多三台不同服务器，失败详情进入既有日志／采集记录。

## 当前实现

目前已支持：

- 按上海、深圳、北京交易所分区发现 TDX 证券主数据，并隔离各分区故障；
- 解析标准 `instrument_id`，同时保留带有效期的数据源标识符；
- 使用半开标识符有效区间，处理观测到的代码复用生命周期；
- 两次完整观测确认消失，避免一次代码列表遗漏立即割裂证券身份；
- 严格的时态标识符解析，将身份区间重叠视为数据损坏，不任意选择；
- 股票与 ETF 的首次全历史导入及逐证券增量日线采集；
- 不受主机时区影响的标准日期语义；
- 股票/ETF 成交量使用股/份，而非 TDX 的手；
- 按行隔离异常 OHLCV，持久化校验结果和重试检查点；
- 在同一事务中发布有效日线、校验证据和重试检查点；
- 在逐证券恢复事务中使用 DuckDB Appender、临时暂存表和集合式日线写入；
- 采集 TDX GBBQ 公司行动，保留原始类别及 C1–C4 血缘；
- 对可疑的空或截断 GBBQ 快照保留上次可信数据，并提供显式修复选项；
- 保存语义已验证且带源记录身份的股本观测；
- 根据原始 OHLC 和公司行动在本地派生前复权/后复权仿射区间；
- 根据内容签名判断复权输入是否变化；普通采集重放未改变内容时，跳过历史加载与重算；
- TDX 概念、风格/地域、指数板块的时态成员关系；
- TDX 和申万行业层级及成员关系，共享采集后按分类体系隔离故障；
- SHA-256 内容寻址的不可变原始归档、`meta.artifact` 血缘、经过校验的历史版本复用，以及损坏包的重新下载恢复；
- 采集 TDX 专业财务 `gpcw.txt` / `gpcw*.zip`，校验清单中的 MD5 和大小；
- 动态、无损解析 gpcw：字段数取自 `report_size/4`，保留原始 float32 位模式及市场标记字节，不猜测其交易所语义；
- 财务归一化保留六位原始代码，不套用当前 SDK 的代码区间规则；
- 在报告期解析时态财务身份，根据数据集语义排除指数，并保存 `resolved` / `pending` / `acknowledged` 记录证据；
- 按不可变数据源证据批量协调 `fundamental.provider_fact`，身份修正时重新归属或删除失效事实，不跨证券重复生成同一版本；
- 分别统计尝试、插入、重新归属和删除的数据源事实数；
- 财务身份治理支持分页查看待解析记录、显式确认及撤销确认；
- 已审核的 80 个 TDX 字段映射，含单季度利润/现金流、股本，以及现金、借款、债券、租赁、税项、利息、权益和再投资明细；新增字段区分期末存量与年初累计，见[核心财务字段](docs/decisions/009-core-financial-fields.md)、[损益/营运资本增补](docs/decisions/012-earnings-working-capital-fields.md)、[现金流/研发字段](docs/decisions/013-cashflow-research-fields.md)、[开源目录与余额/利润增补](docs/decisions/014-open-source-fn-balance-profit.md)及[金融工具与补充供给](docs/decisions/015-financial-instruments-and-supplement-supply.md)；
- CNINFO 公告目录与原文归档、保守的披露日期精度，以及待解析公告的本地重试；
- [北交所2025年代码切换](docs/bse-code-transitions.md)四原文发布与公告日身份核验；保留原代码，缺少唯一时点锚点时仍待解析；
- 显式的数据源事实—公告关联、标准时点基本面物化，以及原始/更正版本的 ASOF 查询；
- 按公告时点查询年度与 TTM，区分单季、累计和存量，缺期返回空值及输入血缘，见[查询规则](docs/decisions/011-annual-and-ttm-windows.md)；
- 持久化采集/计算运行状态：`completed`、`partial`、`failed`、`canceled`；
- 基于数据库的运行状态查询和按版本执行的结构迁移。

专业财务数据源事实的 `announcement_time` 有意允许为空。原始 gpcw 包没有逐记录的权威公告时间，AlphaLake **不会**从抓取时间、文件名或报告期推断。标准时点 `fundamental.fact` 通过独立的 CNINFO 公告证据关联后，由 `materialize-fundamentals` 在本地生成。

证券主数据会发现指数和可转债，但初始股票/ETF 日线与复权流程暂不处理它们；需要先以专门测试验证请求和单位语义。

估值所需行情已支持[版本保留与未复权收盘价导出](docs/valuation-quotes.md)；已新增[类别股本、A/H 市值与市场权重 WACC 估计链](docs/market-wacc.md)，首批安克/茅台；新增[合同债务区间及H股融资事件链](docs/wacc-gap-review-20260909.md)，实际现金滚动、费用重叠及经营范围仍有明确缺口。

面向全 A 股自动估值的[财务就绪度扫描与推进状态](docs/automated-valuation.md)已提供独立命令，缺事实公司也进入本地集合分母。已完成[全本地A股条件估值验收](docs/a-share-automation-acceptance-20260910.md)：5,569家公司全部有处理结论，5,304家核心输入齐全、2,461家产出条件估值，成功结果全部独立复算通过；非当前目标价或逐公司原文全量验收。

[统一公司CLI](docs/company-valuation-entry.md)已提供多政策候选、选择依据及结构化JSON；[历史查询](docs/valuation-run-query.md)可发现已有run ID并保留最新时点并列；[运行比较](docs/valuation-comparison.md)已提供结构化差异及限定WACC归因，下一步及实现边界见[当前优先级](docs/implementation-status.md)。安克的[专项与通用模型口径](docs/anker-recalculation-20260909.md)分别记录，不能用不同政策的数值冒充同一模型更新。

## Agent 使用

已提供 [alphalake-valuation Skill](skills/alphalake-valuation/SKILL.md)，指导支持技能的本地agent调用统一公司CLI、解释候选/缺项并追溯run ID。它需要AlphaLake仓库、后端Python环境、数据库和显式政策配置，不自带财务库、默认估值或第二套计算引擎。

从仓库根目录将技能链接到本地Codex技能目录（已有同名目录时先检查，不覆盖）：

```bash
python3 - <<'PYINSTALL'
import os
from pathlib import Path
source = Path('skills/alphalake-valuation').resolve(strict=True)
skills = Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex')/'skills'
skills.mkdir(parents=True, exist_ok=True)
(skills/source.name).symlink_to(source, target_is_directory=True)
PYINSTALL
```

重新加载支持技能的会话后，可使用`$alphalake-valuation`并给出公司及已有配置；也允许客户端按描述自动选择。其他支持Agent Skills的客户端可安装同一技能目录，具体发现方式由客户端决定。移动仓库后需更新软链接。

安装检查通过不等于客户端已在当前会话发现技能；实际使用仍以客户端技能列表为准。Skill不是MCP服务，目前没有新增MCP接口。程序契约与验收范围见[统一公司入口](docs/company-valuation-entry.md)。

## WACC 参考数据

已提供 `sync-country-risk`：归档达摩达兰 2026 年 7 月工作簿，发布 CN/HK/US 评级法与成熟市场 ERP 共 10 项，支持原子发布、幂等重放和离线核验。具体命令与范围见[国家风险同步](docs/country-risk-sync.md)。另已接入[全球行业 Beta 与人民币国债收益率](docs/beta-yield-sync.md)：94 个行业的 376 项指标和 8 个国债期限点。三条同步链均不改动公司财务事实；已有[固定版本到 WACC／估值桥接](docs/wacc-valuation-bridge.md)，须显式提供公司映射与政策；也可使用[市场权益／估计债务权重分支](docs/market-wacc.md)，不可省略代理假设。另支持[合成评级利差同步与借款成本桥接](docs/credit-spread-sync.md)，首批仅开放安克已审核财务口径。

官方公司行业名单现可用 `alphalake sync-company-industries DB --python /absolute/valuation-python` 归档和发布来源观察，支持 `--offline` 重放；就绪度查询已按可用时点校验并关联标准证券身份，估值政策仍须显式指定，范围和边界见[自动估值主线](docs/automated-valuation.md)。

## 构建与测试

当前项目使用 Go 1.25，这是当前 `github.com/injoyai/tdx` 依赖的要求。

```bash
go test ./...
go build ./cmd/alphalake
```

CI 还会检查 `go mod tidy` 是否产生文件改动，并以 Python 3.12 / [版本与 wheel 哈希锁定的 pypdf](.github/requirements-pdf.txt) 运行[标准事实到估值的双公司验收](internal/ingest/testdata/valuation-chain-2026/README.md)：先执行生产归档、TDX 解析、标准物化及时点查询，再独立核验安克和茅台原始 PDF，最后用标准查询值及明确补充项复算估值。安克六年历史与研发资本化校验另行保留；更正、税项/债务、TTM、核心财务字段四批历史 PDF 校验也在 CI 强制运行。

安克、茅台的原 PDF 模型保留为研究对照；它们通过不等于生产数据链路通过。当前 158 个模型取值中 124 个由标准事实供应，34 个通过独立公告、原文与显式供给契约提供；原 61 个补充项已全部逐项核验，其中 27 个转为 TDX，34 个仍为 CNINFO 补充，不宣称全部 TDX 化。标准链重算后的两位小数每股值不变，源精度差异单列；纯经营现金流等历史缺口仍保留，结果不是当前目标价。见[安克报告](docs/anker-validation-valuation-20260906.md)及[茅台报告](docs/moutai-validation-valuation-20260906.md)。

真实财务样本的离线重放、PDF 归档复核及验证范围见[可重复验收报告](docs/acceptance-20260905.md)。[安克创新财务输入样本](docs/anker-valuation-20260906.md)提供 EBIT 调整、债务和营运资本的原文证据、分析政策与可复算 CSV。

## 命令行

初始化或迁移 DuckDB 数据库：

```bash
alphalake init ./alphalake.duckdb
```

同步一个 TDX 代码。首次运行导入历史；后续运行使用与全市场流程相同的增量、隔离和血缘语义：

```bash
alphalake sync-daily ./alphalake.duckdb sh600519
```

同步当前 TDX 股票/ETF 集合。已有证券从各自最新存储日期或重试边界继续，并重新抓取边界当日。一个交易所分区临时失败不阻止其他正常分区：

```bash
alphalake sync-daily-all ./alphalake.duckdb
```

刷新 TDX GBBQ 公司行动和股本快照：

```bash
alphalake sync-actions ./alphalake.duckdb
```

已有快照时，AlphaLake 默认拒绝请求成功但内容为空或明显截断的 GBBQ 快照。操作人员明确执行修复时，可仅跳过快照大小保护：

```bash
alphalake sync-actions ./alphalake.duckdb --force
```

`--force` **不会**绕过采集错误、身份不匹配或数据库约束。

从已存储的原始 OHLC 和公司行动在本地计算复权区间。标准输入未改变时，按内容签名跳过：

```bash
alphalake calc-adjustments ./alphalake.duckdb
```

刷新 TDX 板块时态分类：

```bash
alphalake sync-classifications ./alphalake.duckdb
```

根据 TDX 行业归属与 `incon.dat` 刷新 TDX 和申万行业层级及成员关系：

```bash
alphalake sync-industries ./alphalake.duckdb
```

同步 TDX 专业财务数据。安全默认值只处理清单中最新的 gpcw 包，原始数据保存在 DuckDB 文件旁的 `raw/` 中：

```bash
alphalake sync-financial ./alphalake.duckdb
```

显式回填清单中的全部历史包：

```bash
alphalake sync-financial ./alphalake.duckdb --all
```

财务同步报告 `facts_attempted`、`facts_inserted`、`facts_reassigned` 和 `facts_removed`。已导入归档的幂等重放不会报告新增变更；后续时态身份修正体现为重新归属或删除，而非重复事实。

复用归档前会校验内容。已保留的包版本损坏或丢失时，系统将其视为缓存未命中，通过数据源大小/MD5 校验路径重新下载并修复本地内容寻址对象，避免该包永久无法处理。

原始 gpcw 代码按包的报告期从 TDX 时态标识符中解析，不按当前 SDK 代码区间推断交易所。gpcw 是公司财务记录，因此候选身份排除指数；真正的公司证券代码冲突保持待解析，不进行猜测。没有唯一时态身份的记录成为持久化 `pending` 证据，不使整个包失败，并可从本地归档重试。

采集 CNINFO 公告目录和符合条件的原文，再在本地物化时点基本面：

```bash
alphalake sync-filings ./alphalake.duckdb
alphalake materialize-fundamentals ./alphalake.duckdb
```

`sync-filings --all` 从 1990-01-01 回填；也可用 `--start YYYY-MM-DD --end YYYY-MM-DD` 指定日期区间。`--metadata-only` 只抓元数据，`--rescan` 强制重扫旧窗口。可加 `--code 600519` 定向补采单只证券：复用目录原始证据中的机构标识，通过 `stock=代码,orgId` 精确筛选；缺失时仅用完整单页关键词响应发现唯一机构，未知或歧义则失败。原始响应仍归档，意外返回其他证券或机构则拒绝完成。代码范围、元数据模式和完整文档模式使用独立检查点；旧版未区分范围/模式或未检查部分分页重叠的完成键不会代替当前验收。

分页检查待解析的财务与公告身份记录：

```bash
alphalake financial-unresolved ./alphalake.duckdb --limit 100 --offset 0
alphalake filing-unresolved ./alphalake.duckdb --limit 100 --offset 0
```

某历史记录经人工审核后暂时仍无法解析时，显式确认对应的不可变归档记录：

```bash
alphalake financial-ack ./alphalake.duckdb 12345 870001 "人工审核后仍缺少历史身份依据"
```

确认操作不会自动发生，必须提供原因。未解析记录重放时保留操作人员审核过的机器原因；后续权威解析成功将替代确认状态，并清除过时的确认信息。

错误确认可以撤销：

```bash
alphalake financial-unack ./alphalake.duckdb 12345 870001
```

撤销确认会在同一事务中将记录恢复为 `pending` 并使包完成检查点失效，强制下次 `sync-financial` 重新评估本地原始记录。

只有所有记录均已解析或显式确认，包才获得完成检查点。

正常市场刷新顺序如下：

```bash
alphalake sync-daily-all ./alphalake.duckdb
alphalake sync-actions ./alphalake.duckdb
alphalake calc-adjustments ./alphalake.duckdb
alphalake sync-classifications ./alphalake.duckdb
alphalake sync-industries ./alphalake.duckdb
alphalake sync-financial ./alphalake.duckdb
alphalake sync-filings ./alphalake.duckdb
alphalake materialize-fundamentals ./alphalake.duckdb
```

以只读方式检查数据库：

```bash
alphalake status ./alphalake.duckdb
```

输出当前/最新结构版本、待执行迁移、校验失败、检查点及近期采集运行。

查看内嵌结构迁移：

```bash
alphalake schema
```

审核补充支持显式修订、撤销与历史查询，镜像原文审核独立于诊断保存；迁移及时间边界见[审核证据说明](docs/reviewed-evidence-history.md)。

当前代码结构版本44，身份域已从 `ref` 改为 `core`，国家风险与ERP分表；旧库升级及固定版本兼容性见[结构迁移](docs/core-risk-migration.md)。主库发布状态与副本验收分别记录。

## 数据布局

数据库位于 `./data/market.duckdb` 时，专业财务原始归档默认布局如下：

```text
data/
  market.duckdb
  raw/
    tdx/
      professional_financial/
        <sha-prefix>/
          <sha256>.txt
          <sha256>.zip
```

`meta.artifact` 中的路径相对于配置的原始数据根目录。

## 原则

- 数据源专有格式止于源适配器。
- 标准记录使用稳定证券身份，不使用数据源代码作为主身份。
- 破坏性的时态变更需要足够完整且重复的数据源证据；不完整或一次性观测不得悄然关闭历史。
- 数据源天然具有独立分区时，各分区故障独立处理。
- 采集血缘记录来源，派生数据是否失效则由标准内容决定。
- 稳定的源文件/文档是不可变证据，本地采用内容寻址；上游仍可提供归档时，可重新验证并修复本地损坏。
- 未复权 OHLC 是主要价格事实；复权值是可复现的派生结果。
- 财务报告期和公告时间是不同概念，不得猜测缺失的公告时间。
- 不得用当前市场代码启发式规则替换原始财务身份依据。
- 数据源语义不完整时，可先保存源事实，再建立标准时点事实。
- 不可变源记录身份与标准证券身份分离：后续身份修正只改变标准关联，不重复生成源证据。
- 保留未解析证据并显式治理；人工确认可撤销且不会自动进行。
- 派生数据集可根据标准事实和输入状态重建。
- 数据质量失败是可查询的数据，不仅是日志。

## 设计文档

- [设计规范](docs/design.md)——已接受的目标架构、规范和主要决策。
- [实现状态](docs/implementation-status.md)——已实现、部分实现、仅有结构和计划中的能力矩阵。
- [架构决策索引](docs/decisions/README.md)——全部决策记录。
- [TDX 日线采集](docs/decisions/001-tdx-daily-ingestion.md)——采集与续传。
- [GBBQ 与复权区间](docs/decisions/002-gbbq-and-adjustment-segments.md)——快照及仿射复权语义。
- [时态分类快照](docs/decisions/003-temporal-classification-snapshots.md)——从观测开始建立分类历史。
- [证券主数据与内容失效判断](docs/decisions/004-security-master-and-content-dirtiness.md)——时态身份、内容状态和日线隔离的原子发布。
- [分区证券主数据韧性](docs/decisions/005-partitioned-security-master-resilience.md)——分区刷新、重复缺失确认、行业故障隔离和无效路径清理。
- [专业财务归档](docs/decisions/006-professional-financial-artifacts.md)——gpcw 不可变证据、无损事实、原始身份、治理及公告时间边界。
- [CNINFO 公告与时点基本面](docs/decisions/007-cninfo-filing-and-pit-fundamentals.md)——公告证据、显式关联、标准事实和 ASOF 查询。
- [CNINFO 公告日期精度](docs/decisions/008-cninfo-announcement-date-precision.md)——日期精度的保守可用时间。
