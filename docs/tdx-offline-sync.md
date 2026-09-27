# TDX本地财务同步

当前入口采用无源数值副本的宽表结构；实际主库发布与全量运行结果见[存储切换验收](financial-storage-cutover-20260925.md)。以下命令的源同步、标准物化和SQLite导出分别计数，不互相冒充完成。

服务器不可用时，使用已有缓存直接同步：

```bash
./alphalake sync-financial workspace/alphalake.duckdb --offline --all
./alphalake materialize-fundamentals workspace/alphalake.duckdb
```

大库须使用[独立服务与内存限制](fundamental-memory-20260919.md)：本轮源入库采用DuckDB 512MiB、单线程、进程1GiB硬上限，构建、同步、物化、测试串行执行。不是把DuckDB查询预算当作进程RSS限制。

`--offline`完全不调用服务器，沿用主库已知证券身份。选择本地`gpcw.txt`所列且实际存在的ZIP，默认最近一期，`--all`处理全部已到报告期的本地包；未来占位包仍跳过。文件名不匹配、坏ZIP或解析错误拒绝；不从文件名猜公告日期。

离线包可以比保存的上游清单更旧：计算实际文件MD5作为版本/检查点，正常解析器核验ZIP结构与CRC，但不冒称该MD5已与当前服务器确认。清单不改写，原文按实际哈希归档，运行明确记录本地/未确认上游状态。若需确认最新版本，使用不带`--offline`的同一命令，按既有hosts策略请求清单并校验。

源入库使用Appender批量写入记录定位，解析身份、定位及完成检查点同包提交；不再逐字段写源长表或在包间重新附加数据库。标准物化从哈希校验后的归档批量重建通用宽列，映射不变时数值与内容签名按包提交；映射变更时全部受影响包及新语义同事务提交，失败回滚。

财务源事实、标准事实和网页兼容状态是三项不同验收。离线同步不补造历史公告、语义审核或缺失的租赁/投资分量；仍须物化并[重新导出SQLite](valuation-sqlite-export.md)，然后检查`/api/database/compatibility/SZSE:300866`。SQLite不会随DuckDB写入自动更新。

原生网页完整交付可使用[统一发布入口](system-delivery.md)的offline模式。`sync-financial --report 新路径`会生成`alphalake-financial-sync-v1`来源维护收据，包含运行ID、缓存回退、错误数量及完整待解析范围；文件已存在时拒绝覆盖。它不改变命令退出码或来源检查点。发布器只能对证据证明不受影响的显式证券范围继续，不能把partial来源称为全量同步完成。
