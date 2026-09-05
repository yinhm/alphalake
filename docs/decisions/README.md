# AlphaLake 架构决策

已接受的目标规范见[设计文档](../design.md)。本目录保留形成当前设计的主要决策过程，以及代码审查推动的变更。

1. [TDX 日线采集与续传](001-tdx-daily-ingestion.md)——标准日期和单位、续传及异常隔离。
2. [GBBQ 快照与复权区间](002-gbbq-and-adjustment-segments.md)——全量快照语义和前/后复权仿射推导。
3. [时态分类快照](003-temporal-classification-snapshots.md)——分类成员历史与不完整快照保护。
4. [证券主数据与内容失效判断](004-security-master-and-content-dirtiness.md)——时态证券身份、基于内容的派生状态和日线原子发布。
5. [分区证券主数据韧性](005-partitioned-security-master-resilience.md)——交易所分区隔离和重复缺失确认。
6. [专业财务归档](006-professional-financial-artifacts.md)——gpcw 不可变证据、无损数据源事实和财务身份治理。
7. [CNINFO 公告与时点基本面](007-cninfo-filing-and-pit-fundamentals.md)——公告证据、数据源—公告关联、标准时点事实和 ASOF 查询。
8. [CNINFO 公告日期精度](008-cninfo-announcement-date-precision.md)——公开目录日期精度下的保守可用时间。

后续 ADR 可以替代早期决策的某一部分，但必须明确说明替代关系；替代不会抹去历史决策依据。
