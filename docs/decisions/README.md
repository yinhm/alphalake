# AlphaLake 架构决策

当前结构见[架构](../design.md)，实际交付见[项目状态](../implementation-status.md)。本目录保留决策形成时的依据，不把旧计划、表名或未执行标记当成当前状态。源BLOB/源字段长表等旧存储方案已被[宽表与轻量定位](../guides/financial-storage-redesign-20260925.md)替代，早期多市场目标不改变当前沪深非金融主线。

1. [TDX 日线采集与续传](001-tdx-daily-ingestion.md)——标准日期和单位、续传及异常隔离。
2. [GBBQ 快照与复权区间](002-gbbq-and-adjustment-segments.md)——全量快照语义和前/后复权仿射推导。
3. [时态分类快照](003-temporal-classification-snapshots.md)——分类成员历史与不完整快照保护。
4. [证券主数据与内容失效判断](004-security-master-and-content-dirtiness.md)——时态证券身份、基于内容的派生状态和日线原子发布。
5. [分区证券主数据韧性](005-partitioned-security-master-resilience.md)——交易所分区隔离和重复缺失确认。
6. [专业财务归档](006-professional-financial-artifacts.md)——gpcw 不可变证据、源证据和财务身份治理；源数值持久化方案已替代。
7. [CNINFO 公告与时点基本面](007-cninfo-filing-and-pit-fundamentals.md)——公告证据、数据源—公告关联、标准时点事实和 ASOF 查询。
8. [CNINFO 公告日期精度](008-cninfo-announcement-date-precision.md)——公开目录日期精度下的保守可用时间。

9. [核心财务字段](009-core-financial-fields.md)——现金、借款、权益和再投资明细、期间区分与真实财报验收。

10. [税项、利息与债务明细](010-tax-and-debt-fields.md)——累计营业利润/税项、债券/租赁、万元换算与源精度。

11. [年度与 TTM 的公告时点查询](011-annual-and-ttm-windows.md)——连续窗口、累计差额、缺期和输入血缘。

后续 ADR 可以替代早期决策的某一部分，但必须明确说明替代关系；替代不会抹去历史决策依据。

- [012：非经营损益与营运资本组成字段](012-earnings-working-capital-fields.md)

- [013：税费现金、营运资本调节项与研发费用](013-cashflow-research-fields.md)

- [014：开源目录与余额/利润增补](014-open-source-fn-balance-profit.md)——参考 mootdx/QUANTAXIS，十二字段进入标准链与估值。

- [015：金融工具映射与 61 项补充供给](015-financial-instruments-and-supplement-supply.md)——万元编码、物化精度修复及独立附注/PIT 供给。

- [016：多市场身份与 WACC 数据表设计](016-multi-market-wacc-data.md)——参考观测与同步、限定身份扩展和估值桥接已实现；公司／证券／上市身份、发布版本、参考观测、历史选择和分步迁移。

- [017：数据库领域与版本边界整理](017-database-design-review.md)——确认 core 命名和风险参考分层，列明映射关联、审核修订、报告主体及迁移验收要求；执行状态以当前架构为准，文内保留当时审核边界。

- [018 标准财务消费与源编号分离](018-standard-financial-consumption.md)

- [019：按报表成批审核标准财务字段](019-complete-statement-field-review.md)

- [020：官方定义批量映射与规范三表快照](020-official-statements-and-snapshots.md)

- [021：招股书历史披露范围与TDX数值来源](021-prospectus-disclosure-coverage.md)

- [022：财务数值可用性与公告日期分离](022-financial-availability-and-disclosure.md)——TDX日期直接采用，当前数值不依赖CNINFO关联；历史查询单独限制。
