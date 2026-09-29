# 文档索引

先读[当前项目状态](implementation-status.md)；需要了解数据职责和模型边界时读[架构](design.md)。本文只导航，不重复维护验收数量。

| 层级 | 内容 | 更新规则 |
|---|---|---|
| 项目入口 | 仓库README、本索引、当前状态、架构 | 当前能力和优先级只在状态页汇总，不追加逐轮日志 |
| [操作手册](guides/) | 同步、资源、查询、导出、发布与审核 | 使用当前命令与契约；旧步骤移出，不保留双路径 |
| [估值方法](valuation/README.md) | 原生参考、自动候选、输入口径、资本效率 | 区分方法、公司证据和政策假设；保留适用边界 |
| [架构决策](decisions/README.md) | 已接受的设计选择及替代关系 | 不抹去决策历史；当前实现以代码和状态页为准 |
| [历史验收](history/README.md) | 旧迁移、样本、阶段性审核和失败记录 | 带历史边界，不作为日常操作指南 |
| [机器验收收据](acceptance/) | 已冻结JSON | 原字节保留，不当成当前状态 |

## 操作手册

- **更新与发布**：[离线财务同步](guides/tdx-offline-sync.md)、[节点轮询](guides/tdx-failover.md)、[报告期行情窗口](guides/valuation-quote-window.md)、[原生发布与恢复](guides/system-delivery.md)。
- **数据与资源**：[SQLite导出](guides/valuation-sqlite-export.md)、[workspace布局](guides/workspace-layout.md)、[内存隔离](guides/fundamental-memory-20260919.md)、[财务存储性能门槛](guides/financial-storage-redesign-20260925.md)。
- **估值调用**：[公司入口](guides/company-valuation-entry.md)、[运行查询](guides/valuation-run-query.md)、[运行比较](guides/valuation-comparison.md)、[Agent Skill](../skills/alphalake-valuation/SKILL.md)。
- **证据治理**：[审核修订与撤销](guides/reviewed-evidence-history.md)、[三表标准契约](decisions/020-official-statements-and-snapshots.md)、[招股披露范围](decisions/021-prospectus-disclosure-coverage.md)。

## 阅读与维护边界

历史报告中的“当前”、数值、源码路径、迁移命令和待办属于当时提交，不能据此初始化新库、补零或改估值政策。`valuation/research`、测试夹具和机器收据是冻结证据，不在文档整理中批量改写；旧链接按原提交查看，现存说明可在历史索引按文件名查找。

长期规范写入AGENTS，未完成维护项写入[待办](review-minor-backlog.md)，动态运行结果写入workspace/derived。新一轮工作应更新已有主题页；只有独立契约或必要的验收证据才新建文档，不再为每轮进度新开一个当前说明页。
