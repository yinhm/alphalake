# TDX本地财务同步

当前历史扩量已暂停：离线入口可用不代表现有字段长表性能达标。先按[存储重设计规格](financial-storage-redesign-20260925.md)改造并验收，再恢复主库全量同步及物化；以下为命令语义，不是本轮全量完成记录。

服务器不可用时，使用已有缓存直接同步：

```bash
./alphalake sync-financial workspace/alphalake.duckdb --offline --all
./alphalake materialize-fundamentals workspace/alphalake.duckdb
```

大库须使用[独立服务与内存限制](fundamental-memory-20260919.md)：本轮源入库采用DuckDB 512MiB、单线程、进程1GiB硬上限，构建、同步、物化、测试串行执行。不是把DuckDB查询预算当作进程RSS限制。

`--offline`完全不调用服务器，沿用主库已知证券身份。选择本地`gpcw.txt`所列且实际存在的ZIP，默认最近一期，`--all`处理全部已到报告期的本地包；未来占位包仍跳过。文件名不匹配、坏ZIP或解析错误拒绝；不从文件名猜公告日期。

离线包可以比保存的上游清单更旧：计算实际文件MD5作为版本/检查点，正常解析器核验ZIP结构与CRC，但不冒称该MD5已与当前服务器确认。清单不改写，原文按实际哈希归档，运行明确记录本地/未确认上游状态。若需确认最新版本，使用不带`--offline`的同一命令，按既有hosts策略请求清单并校验。

源入库按证券代码范围限制暂存及合并工作集，各批仍属于同一个包事务；后批失败会回滚前批，未完成包不推进检查点。包间重新附加持久库，释放索引常驻内存；这是[DuckDB官方建议](https://duckdb.org/docs/lts/guides/performance/indexing)的内存处理方式，不删除索引或放宽约束。

财务源事实、标准事实和网页兼容状态是三项不同验收。离线同步不补造历史公告、语义审核或缺失的租赁/投资分量；仍须物化并[重新导出SQLite](valuation-sqlite-export.md)，然后检查`/api/database/compatibility/SZSE:300866`。SQLite不会随DuckDB写入自动更新。
