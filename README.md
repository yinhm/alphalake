# AlphaLake

AlphaLake 是面向投资研究、本地优先且可复现的金融市场数据基础设施。

它通过多个数据源适配器采集数据，将记录归一化为标准模型，在 DuckDB 中存储分析数据，并保留重建、校验和派生数据集所需的血缘信息。数据源提供稳定文件时，系统将其保存为不可变的原始证据。

## 初始范围

- TDX 提供的 A 股日线 OHLCV 和市场参考数据
- TDX 提供的公司行动、股本变动、分类及指数/板块成员关系
- 以 TDX 专业财务数据作为主要结构化基本面来源
- 以 CNINFO 公告作为权威校验与血缘来源
- 以 DuckDB 作为标准分析存储

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
- 已审核的 TDX FN230–FN238 字段映射；
- CNINFO 公告目录与原文归档、保守的披露日期精度，以及待解析公告的本地重试；
- 显式的数据源事实—公告关联、标准时点基本面物化，以及原始/更正版本的 ASOF 查询；
- 持久化采集/计算运行状态：`completed`、`partial`、`failed`、`canceled`；
- 基于数据库的运行状态查询和按版本执行的结构迁移。

专业财务数据源事实的 `announcement_time` 有意允许为空。原始 gpcw 包没有逐记录的权威公告时间，AlphaLake **不会**从抓取时间、文件名或报告期推断。标准时点 `fundamental.fact` 通过独立的 CNINFO 公告证据关联后，由 `materialize-fundamentals` 在本地生成。

证券主数据会发现指数和可转债，但初始股票/ETF 日线与复权流程暂不处理它们；需要先以专门测试验证请求和单位语义。

## 构建与测试

当前项目使用 Go 1.25，这是当前 `github.com/injoyai/tdx` 依赖的要求。

```bash
go test ./...
go build ./cmd/alphalake
```

CI 还会检查 `go mod tidy` 是否产生文件改动。

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

`sync-filings --all` 从 1990-01-01 回填；也可用 `--start YYYY-MM-DD --end YYYY-MM-DD` 指定日期区间。`--metadata-only` 只抓元数据，`--rescan` 强制重扫旧窗口。元数据模式和完整文档模式使用独立检查点；旧版未区分模式的检查点会被忽略，首次升级运行将重新扫描相关窗口。

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
