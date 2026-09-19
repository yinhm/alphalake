# 通用财务字段正式发布（2026-09-18）

生产标准消费链已从FN业务键切换为通用财务字段，分层规则见[ADR 018](decisions/018-standard-financial-consumption.md)。本轮不增加财务数值或估值假设；源解析、映射及血缘仍保留TDX编号。

## 数据库与接口

- 财务主库：`workspace/auto-valuation-20260909/market.duckdb`，schema45→46；备份同路径加`.pre-standard-fields-20260918`。实际WACC参考库仍为已验收的44，未修改。
- 独立标准目录`fundamental.field`共81项；源目录9项期间元数据规范化，32,573条股本事实的期间标签改为`instant`。2,048,273条标准事实的数值、身份和原始血缘不变；没有新增、删除财务事实。
- 标准TTM依据通用目录计算，不读取源映射目录决定期间算法；在无标准事实的证券上仍不产生窗口，就绪度保留全证券分母。通用目录可以为无该项事实的公司返回缺口，不以来源映射有效期隐藏标准字段。
- 财务导出为`alphalake-valuation-v2`，准入导出为`alphalake-readiness-v2`；`field`为通用字段，来源编号另留`source_provider_field`。两种只读出口要求schema46，旧库显式报升级要求。
- API、标准窗口读取、专项/通用估值、股权桥接、现金检查、准入、审核政策及已保存预测核验均使用通用字段。旧v1请求须显式迁移后生成新运行，原文件及原run ID不改写。

## 数值核验

固定财务及股本基期2026-06-30，信息截止2026-09-18T13:30:43Z，使用[原经济政策、通用字段配置](../valuation/examples/batch-disposal-main-20260918.json)。WACC仍分别为旧固定基线8.0753671970%、5.7531403615%，不是本日市场WACC或目标价。

| 公司 | 迁移前后条件值（元/股） | 新运行ID |
| --- | ---: | --- |
| 安克创新300866 | 127.1544634851 | `5ad4b9ee1a06e2162844a1029219ff2e5b39d327dbfff99f29f22c8b3b721840` |
| 苏泊尔002032 | 45.7299290863 | `15345fc25c4890a8beceae01a176737942603eb92d72234cd7b4ab14de8875e0` |

两份完整报告仅有已审核资产桥接分量的键名变化；按明确的字段对应关系转换旧键后，报告逐项一致，并经过独立Decimal复算。不能声称原始报告字节完全相同。安克处置现金TTM仍为17,350元、购建现金减处置现金仍为319,616,930元；标准缺期及公司资本倍率拒绝保持原边界。

[发布回执](acceptance/standard-fields-publication-20260918.json)记录全部旧表的期望变换后摘要、库文件哈希、备份、字段数及运行对应关系。只允许新增标准目录、046版本记录、上述期间标签和映射期间语义变化；其他表保持全列内容摘要不变。摘要使用行数及哈希XOR/和，不冒称密码学逐行证明。金额篡改拒绝有回归。

发布使用既有`check-core-publication --standard-fields`：先验证副本及两家公司估值，再锁定/核对库文件哈希、保留备份并原子发布。正式库重开后，两家公司统一CLI输出与候选逐字节相同，原库备份及发布文件哈希保持匹配。

## 验证与复验

Go全套测试、构建、相关vet通过。Python全套首次438项通过、3项旧契约/标签断言失败；修正后三项及其所属相关测试均复验通过，合计441项通过、4项既有外部Excel样本缺失跳过、7条既有依赖警告。没有将失败项忽略或标skip。沿用现有venv、无新增依赖，CI既有全量pytest与Go测试覆盖本轮新增回归；主库发布不由CI自动执行。

```bash
PYTHONPATH=valuation/backend \
ALPHALAKE_VALUATION_RUN_DIR=workspace/standard-fields-20260918/runs \
workspace/anker-agent-adapter-20260906/venv/bin/python -m tools.company_valuation \
 workspace/auto-valuation-20260909/market.duckdb 300866 \
 --period 2026-06-30 --as-of 2026-09-18T13:30:43Z \
 --policy valuation/examples/batch-disposal-main-20260918.json --alphalake ./alphalake
```

苏泊尔将代码换为002032。本次发布曾使用显式转换工具；当前已删除工具，转换记录和历史复验方式见ADR 018；旧运行索引不能直接作为v2重放成功证据，应保留并另建新索引。

## 研究链补充验收

上述发布轮完成生产标准查询与估值消费链。随后已完成[研究与文档消费分层核查](standard-fields-consumption-audit-20260918.md)：经营计算通过独立源适配使用通用字段，冻结原件、源位校验及历史评分结果保留。后续443项Python测试、Go测试/构建/vet均通过；不改写本次数据库发布轮的历史验收计数。
