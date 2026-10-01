# 估值方法与输入

共享引擎以达摩达兰原始方法为依据；已核实的公式、显式代理与未闭合实现边界见[方法复核](methodology-contract.md#标准输入与方法复核2026-09-29)。估值产出、公式一致性和经济适用性分别验收，不能用成功率或贴近股价证明假设准确。产品当前范围及未完成项见[项目状态](../implementation-status.md)。

| 主题 | 当前入口 | 需要注意 |
|---|---|---|
| 架构与引擎改动 | [边界、逐提交台账及原始方法依据](engine-change-ledger.md) | 27个引擎提交全部入账；本地政策及导入前证据缺口不冒充全部认证 |
| 引擎冻结与审批 | [valuation TODO](../../valuation/TODO.md) | 仅修明确BUG；其余方法、政策及接口调整等待审批 |
| 五项通用方法契约 | [输入、经营范围、再投资、终值及边界](methodology-contract.md) | 结构化检查与条件对照；不将可计算当作经济依据通过 |
| 数据接入spec | [实际消费与转换契约](data-contract.md) | TTM/累计/单季、单位、余额、研发队列及会话；任何对接前先核对 |
| 国内行业与估值关联 | [TDX、申万及跨体系映射调研](industry-association.md) | 国内分类与估值参考分别计数；申万主分类及新映射默认政策尚未上线 |
| 原生参考与版本 | [参考桥接](native-reference-bridge.md) | 默认US口径保留，主库→SQLite→会话参考有版本；不是公司市场WACC自动认证 |
| 人民币显式场景 | [WACC与经营候选](native-cny-policy.md) | 来源、折现及经营假设分别保存，不与网页默认混为价格更新 |
| 自动选择与合法变更 | [基础选择规则](native-assumption-selection.md) | 规则只是条件候选；合法输入变更须重建基线，不静默混用口径 |
| 跨公司复验 | [统一情景](native-uniform-scenarios.md) | 固定分母、持续期与利润率场景，不逐个公司调参挑结果 |
| 资本效率 | [投入、回报及过渡](capital-efficiency.md) | 公司/行业存量倍率、未来新增效率、终值回报分开 |
| 历史资本分量 | [标准资本核算](native-capital-definition.md) | 算术闭合不等于经营分类或预测倍率获批 |
| 资产代理 | [已批准账面代理](valuation-book-proxies.md) | 现金/投资/债务范围限制保留，不冒充完整公允价值 |
| 准入与缺项 | [模型准入原则](valuation-input-gates-20260925.md) | 窗口不等于门槛，事实/补充/预测分别处理；现行列定义以[SQLite契约](../guides/valuation-sqlite-export.md)为准 |
| 预测有效性 | [复验纪律](valuation-accuracy.md) | 历史回测暂非主线，既有失败及前视边界仍保留 |

实际调用和运行标识见[公司入口](../guides/company-valuation-entry.md)与[运行查询](../guides/valuation-run-query.md)。单公司旧估值、早期WACC实验和逐轮金额核对统一进入[历史验收](../history/README.md)，不作为当前目标价。
