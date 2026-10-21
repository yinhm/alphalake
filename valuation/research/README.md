# 冻结研究与回归证据

这里保存早期方法试验、真实来源切片、显式条件及契约重建证据，**不是当前研究报告目录，也不是运行时数据源**。正式报告、图表、模型与版本发布统一在`workspace/research/<id>`，通过`/research`阅读；操作见[研究交付](../../docs/guides/research-delivery.md)，最新范围见[项目状态](../../docs/implementation-status.md)。

下列索引只归并阅读入口，不改写原协议、结果或失败。目录名中的`current`指创建当时的契约，不承诺始终兼容今天的运行时。旧README中的路径、编号、命令和“待办”属于冻结历史，不能据此恢复已撤销政策、扩历史或重新启动参数筛选。

## 仍被当前回归消费的重建证据

| 目录 | 用途 |
| --- | --- |
| [财务契约重建](current-contract-20260919/README.md) | 标准输入与资产／处置现金等回归，保留原／新文件哈希及一次性转换出处 |
| [国家参考重建](current-references-20260930/README.md) | 当前参考输入、公司审阅及增量估值等回归；不复制全部历史研究 |
| [Agent适配预期](agent-adapters-current-contract-20260930/README.md) | 安克／茅台真实适配预期，原源值及冻结计算分开保留 |

是否可清理按实际调用方判断，不以目录日期或名称判断。对应默认／真实来源回归范围见[测试约定](../../docs/guides/testing.md)。

## 已结束的方法研究

| 主题 | 冻结入口 | 结论边界 |
| --- | --- | --- |
| 收入增长与持续期 | [历史CAGR](tdx-revenue-cagr/README.md)、[历史误差选择](tdx-revenue-selection/README.md)、[同窗TTM同比](tdx-ttm-yoy/README.md)、[半年增长](tdx-half-growth/README.md)、[扩大样本](tdx-growth-expanded/README.md)、[多起点](tdx-multi-origin/README.md)、[多年增长](tdx-multiyear-growth/README.md)、[增长校准](tdx-growth-calibration/README.md) | 固定协议及停止条件保留；不把局部改善当成已批准的默认预测 |
| 零增长与不确定性 | [两／三年验证](tdx-zero-growth-validation/README.md)、[一至三年留出反证](tdx-zero-growth-horizons/README.md)、[利润校准](tdx-zero-calibration/README.md)、[历史误差带](tdx-past-error-bands/README.md) | 两／三年的局部结果不能外推为统一持续期或完整DCF准确度 |
| 利润与研发 | [正常化利润率](tdx-normalized-margin/README.md)、[亏损恢复](tdx-loss-recovery/README.md)、[研发与增长](tdx-rd-growth/README.md)、[研发与利润](tdx-rd-profit/README.md) | 未获采用的候选与真实亏损保留；不按结果调阈值或剔除公司 |
| 现金流与再投资 | [资本开支](tdx-capex-forecast/README.md)、[营运现金](tdx-working-cash-forecast/README.md)、[经营现金流预测](tdx-operating-cash-forecast/README.md)、[OCF复算](tdx-operating-cash-replication/README.md)、[现金／收入比较](tdx-cash-revenue-comparison/README.md)、[资本分量](tdx-capital-inputs/README.md) | 调节贡献不等于完整营运资本，失败结果不转成生产规则 |
| 外部经营线索 | [固定券商收入](analyst-revenue-pilot/README.md)、[最新券商选择](analyst-revenue-latest/README.md)、[管理层目标](management-targets-five/README.md)、[行业历史参考](damodaran-growth-history/README.md) | 券商／管理层目标与行业均值不是公司事实，也不是经验证的参数选择器 |

历史预测研究已退出当前主线，默认开发测试不全量重放。尚未通过的候选不因本次整理获得批准；需要继续研究时重新明确目标、冻结样本和信息截止，不复用已暴露留出作为全新证据。

## 历史数据及交付核验

| 主题 | 冻结入口 |
| --- | --- |
| 原始财务与标准历史 | [历史源核验](tdx-history/README.md)、[起点输入](tdx-origin-inputs/README.md)、[五家公司标准历史](standard-history-five/README.md)、[2025H1标准历史](standard-history-2025H1/README.md)、[持续经营样本](continuing-operations-five/README.md) |
| 方法输入与条件交付 | [方法闭环](method-closure-20260912/README.md)、[公司输入](company-inputs-20260917/README.md)、[已审核资产](reviewed-assets-20260917/README.md)、[长期资产处置现金](disposal-cash-20260918/README.md)、[自动估值阶段交付](automatic-valuation-20260917/README.md) |

这些阶段后来可能被新的标准字段、契约或冻结规则替代，当前做法以[数据契约](../../docs/valuation/data-contract.md)和[引擎台账](../../docs/valuation/engine-change-ledger.md)为准，不将旧输入或旧结论拼接成最新估值。

## 保留与复验

- 仓库保留唯一源切片、协议、失败结论、显式条件与来源收据；它们是冻结证据的例外，不是继续把新动态研究写入Git的理由。
- 老研究按其冻结实现提交及对应源版本复验；迁移／转换从原收据定位，不给当前代码恢复兼容入口。保留的证据不自动保证所有旧下载路径仍可用。
- workspace中的过期候选、全量响应或重复附件按[清理规则](../../docs/guides/workspace-layout.md#清理与迁移)退役，保留路径、哈希、原因及复验限制。不能仅凭“research”目录名批量删除当前测试或正式报告仍在引用的文件。
