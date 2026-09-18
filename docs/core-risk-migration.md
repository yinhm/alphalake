# core 身份域与风险参考拆分

迁移043/044已实现，代码最新结构版本44；生产财务主库仍为39，本轮未发布主库。历史迁移保持原样，当前 SQL 入口使用新名称。

## 身份域

迁移043将 `ref` 中公司、证券、外部标识、交易所、listing 及 listing 标识移入 `core`；交易日历移入 `market.trading_calendar`。完成后删除旧 `ref` schema，不保留可写的双份身份表或隐式兼容视图。

当前 DuckDB 不支持 `ALTER SCHEMA ... RENAME` 或 `ALTER TABLE ... SET SCHEMA`。迁移执行器仅为043在同一事务中创建 `core` 及五个序列，起点大于旧序列已消耗编号及已有行最大 ID；043 SQL 使用完整 DDL 重建约束、复制所有列，再按外键依赖顺序删除旧对象。任何错误回滚整个043；旧 schema 存在额外用户对象时拒绝删除，不使用 CASCADE。

迁移保留全部原 ID 和数值，不改变证券生命周期、公司映射或市场准入。更名不能解释成已完成全市场公司／listing 身份；财务仍按证券组织。历史研究快照和旧迁移中的 `ref` 是当时的结构，保持不改；维护中的 Go 入口已经切换到 `core`。旧查询脚本使用新库前需更新名称。

## 风险参考

| 物理表／视图 | 指标与作用 |
| --- | --- |
| `reference.country_risk` | 国家／地区的 `country_risk_premium`、`sovereign_default_spread` |
| `reference.equity_risk_premium` | `mature_market_erp`、`total_equity_risk_premium`，保留来源方法 |
| `reference.risk_observation` | 上述两表的只读联合视图，用于完整发布校验和既有出口 |

两表保持原观察列、ID、归档及发布版本，继续共用既有 `reference.country_risk_id_seq`；共享序列沿用旧名，保证两表自动生成的 ID 不相互冲突。没有重新编号或重新发布参考版本。新同步按指标路由到对应物理表，两个表及检查点仍原子发布；旧版本重放通过联合视图检查完整性。

现有 `alphalake-wacc-references-v1/v2` JSON 中 `country_risk` 数组仍是原十项发布数据，保持固定版本契约、哈希和 Python 消费兼容；物理表拆分不强迫旧请求重新绑定。需要直接 SQL 查询全部原风险指标时，使用 `reference.risk_observation`。新表不扩大源指标白名单，不假装已经接入所有国家或所有 ERP 估计方法。

## 升级与验收

先备份、在副本运行新 CLI `alphalake init COPY_DB`，比较身份与受影响导出，关闭重开后验收，再安排生产发布。新程序不通过只读查询自动迁移旧库。风险参考可能位于独立参考库，也须分别升级；不能只升级财务库。

[真实副本验收记录](acceptance/core-risk-upgrade-20260918.json)区分两个库：

- 财务主库副本：52,480 个 instrument 和 52,480 个 identifier 的所有列、排序内容哈希完全一致；其余五张被迁移表在该库为空，非全市场公司或 listing 验收。
- 安克2026H1、信息截止2026-09-18零点的财务导出：394,292 字节完全一致。
- 独立 `references-refresh-live.duckdb` 副本：四类 WACC 参考自动选版导出207,772字节完全一致，原观测／发布 ID 保留。
- 比较基线为旧程序先升级至42的副本，对比043/044后44版本；不将39→42的准备过程冒充本轮审核迁移验收。

自动回归另覆盖非空公司／listing／日历、大于 float64 精确范围的身份、已消耗序列、重开重放、外键拒绝、额外对象导致的事务回滚及跨风险表指标拒绝。原真实源发布、标准财务链和估值回归继续运行。本轮没有更改估值公式、政策或数据来源。
