# ADR022：财务数值可用性与公告日期分离

状态：2026-09-29用户明确批准。取代ADR007中“CNINFO关联后才物化”的当前数值门槛；不修改估值公式、字段单位和零策略。

TDX官方手册定义了`financial_report_announcement_date`（源维护编号FN314），日期格式YYMMDD；本项目已有[官方目录](../../internal/source/tdx/financial/catalog.csv)和[原始文档归档](../../internal/source/tdx/financial/testdata/README.md)。研究工具使用过该日期，生产物化未接通是实现缺口。

- 数值按源身份、报告期、标准字段及单位独立物化。缺公告、关联pending/ambiguous、缺日期本身不阻断当前数值；来源记录重复冲突、非法金额及已知语义歧义仍拒绝。CNINFO公告歧义不等于TDX金额冲突。
- 有效TDX日期优先；CNINFO已知日期可补充；未知或非法日期留空。每条宽行记录`announcement_source`，不伪造CNINFO公告ID。日期精度供历史查询采用中国次日零点，不能当成真实发布时间。
- 当前SQLite导出按所需报告窗口读取现有标准值，不按公告日期过滤；版本按归档取得顺序选择。明确标记`current_standard_snapshot_not_pit`，不能将后来取得的修订包冒充当时已留存版本。
- 显式历史日期查询仍执行日期边界，未知日期不自动可见；严格系统PIT还需历史版本证据，单个日期字段不能证明。TDX日期可能仍为原公告日期；已有明确更正证据时，历史查询不得早于更正公告，当前估值仍可使用该修订值。历史限制不得反向删除当前数值。
- 不扩大PDF数值来源，不强制全量补采CNINFO，不因研发队列需要而改变估值方法或扩大历史窗口。

schema55以`upgrade-financial-availability <未发布副本>`一次性升级为schema56，再从已有归档执行`materialize-fundamentals`。标准宽表不新增数值副本，公告引用和时间改为可空，新增记录级日期来源；旧数值、来源定位和审核证据需比较保留。SQLite v9显式重导出，旧版本按原提交复验，不加兼容分支。

本轮执行结果统一维护在[项目状态](../implementation-status.md)，性能、重放及失败证据放workspace/derived，文档不冒称代码修改即完成真实库发布。
