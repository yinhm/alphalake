# AlphaLake

面向沪深普通非金融企业的本地金融数据与条件估值系统。主要链路：**TDX → DuckDB标准事实 → 兼容SQLite → 原生valuation网页/API**。当前不推进金融专项模型或多市场扩展，默认不纳入北交所；估值方法以达摩达兰原始资料为准，事实、代理和预测假设分层。

当前无实际生产部署。最新主库为schema55、约341MiB；当前100家公司样本财务87家准入、13家按缺项拒绝；默认参考齐备86家，另1家须显式选择行业/国家。此比例不是全市场数据完整率或估值准确率。历史资本分量扩展后，本轮另补六项余额历史，主库新增38,282标准值且既有值不变；详见[当前实现状态](docs/implementation-status.md)。

## 使用与维护

| 目的 | 入口 |
|---|---|
| 理解系统边界与本轮进度 | [系统交付与闭环验收](docs/system-delivery.md) |
| 从本地缓存同步财务 | [离线同步](docs/tdx-offline-sync.md) |
| 定位通用数据缺口 | [原生覆盖基线与诊断](docs/native-coverage.md) |
| 原生参考、默认口径与会话版本 | [参考接入](docs/native-reference-bridge.md) |
| 人民币WACC及经营候选对照 | [显式政策与验收](docs/native-cny-policy.md) |
| 自动选择条件假设 | [选择规则、拒绝与验收](docs/native-assumption-selection.md) |
| 审阅增长、利润率与资本需求 | [经济输入审阅](docs/native-policy-economic-review.md)、[资本口径核算](docs/native-capital-definition.md) |
| 为原网页准备数据 | [SQLite导出、字段与准入](docs/valuation-sqlite-export.md) |
| 按显式政策估值 | [统一公司CLI](docs/company-valuation-entry.md) |
| 查询、解释估值变化 | [历史运行](docs/valuation-run-query.md)、[运行比较](docs/valuation-comparison.md) |
| Agent调用 | [alphalake-valuation Skill](skills/alphalake-valuation/SKILL.md) |
| 数据位置、日常资源约束 | [workspace布局](docs/workspace-layout.md)、[内存隔离](docs/fundamental-memory-20260919.md) |
| 历史成果及证据 | [历史实现记录](docs/implementation-history-20260927.md) |

唯一权威库是`workspace/alphalake.duckdb`，财务及已接入参考数据合库；网页读取`workspace/derived/valuation.sqlite`。TDX是结构化数值主源，CNINFO提供披露身份、时间及核验。动态数据只写workspace，原始归档保留，SQLite可从主库重建。

网页保留原操作流程，用户可以修改预测参数。模型默认值、现金/投资及多股类市值代理均不等于公司已核验事实；当前不能称为全市场完全自动估值。同步主库不会自动更新网页，同步、验收、发布由[原生交付入口](docs/system-delivery.md)串联；已完成100家离线闭环，在线最新性及输入经济合理性仍须分开验证。

## 来源与构建

估值应用[`valuation/`](valuation/README.md)源自[chrisuzy/Investment_Valuation_Agent](https://github.com/chrisuzy/Investment_Valuation_Agent)。感谢原作者Chirs Yu Zhang及贡献者，导入版本、MIT许可证与Credits见[来源记录](valuation/UPSTREAM.md)。继续复用其共享引擎和原页面，不维护第二套DCF引擎。

Go版本以`go.mod`为准。构建及测试命令：

```bash
go test ./...
go build ./cmd/alphalake
PYTHONPATH=valuation/backend .venv/bin/python -m pytest -q valuation/backend/tests
```

本机全套测试和大库任务必须在已核验MemoryMax的独立systemd服务内串行运行，不能直接将上面三条同时运行。CI执行Go、Python回归及多组离线PDF证据校验；测试通过不等于全市场逐公司原文审核。

## 命令行

初始化当前 DuckDB 数据库（已有schema55直接打开；schema54先执行`upgrade-native-references`；更早版本按原提交显式转换或可验证重建，先保存不可重建证据和审核记录）：

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

只补估值报告期所需行情（证券身份须已入库）：

```bash
alphalake sync-valuation-quotes workspace/alphalake.duckdb --symbols sh600004,sh600006 --period 2026-06-30
```

仅保存报告日前14个自然日至当日的行情观测，不推进完整日线及其续传检查点；详见[窗口同步](docs/valuation-quote-window.md)。

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

输出数据库与代码结构版本、校验失败、检查点及近期采集运行。版本不符时须保留证据并重建，不自动升级。

查看当前结构基线：

```bash
alphalake schema
```

审核补充支持显式修订、撤销与历史查询，镜像原文审核独立于诊断保存；迁移及时间边界见[审核证据说明](docs/reviewed-evidence-history.md)。

当前仅维护schema55，基本定义见 [schema.sql](internal/store/duckdb/schema.sql)，财务宽表及查询见 [financial_snapshot.go](internal/store/duckdb/financial_snapshot.go) 和 [financial_queries.sql](internal/store/duckdb/financial_queries.sql)。财务与参考数据已归并主库并接入[全量TDX源目录](docs/tdx-financial-catalog-20260919.md)，原有财务与参考内容不变，新增标准映射及本地事实的最新范围见[接入验收](docs/decisions/020-official-statements-and-snapshots.md)。旧迁移链及专项发布工具退出当前代码，严格历史复验使用原提交；处置记录见[兼容清理](docs/compatibility-cleanup-20260919.md)。

## 数据布局

动态数据统一放在 `workspace`，唯一权威库同时保存财务及已接入的达摩达兰等参考数据：

```text
workspace/
  alphalake.duckdb
  tdx-cache/             # gpcw.txt及直接落盘的gpcw*.zip
  cninfo/                # 公告目录与财报原文
  damodaran/             # 官方工作簿、来源记录及估值参考文件
  <其他来源>/objects/
  derived/
    valuation.sqlite     # 网页派生快照
    valuation-runs/      # 不可变估值运行
    cleanup-audit/       # 清理、ID及归档路径迁移记录
```

`meta.artifact.local_path` 相对于数据库所在目录；不再附加 `raw/`。TDX命名缓存直接落盘，已引用的不可变原始证据保存在 `tdx/`；上游未变时复用，本地回退会记录诊断及partial状态，不声称最新。详见[工作目录与缓存约定](docs/workspace-layout.md)。Python动态数据根目录可由 `ALPHALAKE_WORKSPACE` 显式指定，默认使用项目workspace；运行环境位于 `.venv`。上游原有冻结模板、测试资料不作为可写缓存。

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
