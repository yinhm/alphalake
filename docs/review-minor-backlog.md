# Review 待办小点

2026-09-10 文档对齐：编号8要求的独立分母已由`valuation-readiness`实现，无事实证券纳入5,569家集合，见[全市场验收](a-share-automation-acceptance-20260910.md)，故移出待办；其余项目沿用各自证据，未在本轮重新审计。只记录尚未关闭的范围，不把已修复事项重新列为缺口。

主线以[当前优先级](implementation-status.md)为准；原61项补充取值已完成逐项审核，不再作为未审核总清单。本清单单独排期，不代表以下项目立即实施；编号保留便于追溯。

| 编号 | 尚未关闭的范围与证据 | 处理时机及关闭标准 |
| --- | --- | --- |
| 3 | [安克样本 README](../internal/ingest/testdata/anker-valuation-2026/README.md) 已说明跨行标签和租赁负债行截至下一“长期借款”行的边界，但未完整列举 OCF 标签变体等版式特例。 | 下次扩充提取规则时补全“报告/页码、表格、特例原因、限定边界”清单；保持样本专用脚本，不因此建设通用解析器。 |
| 4 | [真实 TTM 测试](../internal/ingest/real_ttm_test.go) 使用 `float64` 精确相等；季度流量另有 PDF 十进制金额和源 float32 的 ULP 界限交叉核对。 | 暂观察。引擎升级或聚合顺序变化时复核；若调整断言，应根据存储精度和计算误差确定界限，不任意放宽金额容差。 |
| 5 | [真实更正样本](../internal/ingest/real_correction_sample_test.go) 未直接断言 `corrects_filing_id` 指向原公告。[存储层测试](../internal/store/duckdb/filing_test.go) 已有该字段断言，故不是完全无覆盖。 | 下次更正样本维护时补实际公告之间的前序 ID 断言，锁定集成链路。 |
| 6 | [模拟端到端测试](../internal/ingest/pit_end_to_end_test.go) 的 230 字段记录对应 25 个映射候选，以 22 个插入和 3 个拒绝等计数断言，字段组成不够自明。 | 随测试维护补明确的预期字段集合及拒绝原因；保留计数检查，避免由被测映射目录自动生成期望值而掩盖映射回归。 |
| 7 | [标准事实物化](../internal/store/duckdb/fundamental_materialize.go) 每次运行插入拒绝诊断，同一持续歧义会累积多行；事实重放仍幂等。 | 诊断量造成查询或存储压力时再决定去重/汇总口径，保留运行追溯能力；当前不新增去重机制。 |
| 9 | [年报工作流测试](../internal/ingest/real_financial_workflow_test.go) 的六份 PDF 正文归档检查依赖 `ALPHALAKE_ACCEPTANCE_RAW`。未设置时会记录日志并跳过，测试不失败；不是毫无提示。 | 单独补自包含验收。区分已固化金额校验与正文归档完整性校验；要关闭此项，六份正文证据须在干净环境可取得并强制校验，不能用 CSV 金额断言冒充 PDF 正文覆盖。 |
| 10 | 迁移 021 清除译本相关更正前序后，重链仍依赖目录重放；`status` 尚无待重放提示。现有迁移说明和 ADR 007 已列出操作流程。 | 需要自动化升级巡检时再增加准确的待重放标记及提示；不能将所有 NULL 前序当成待重扫，因为同日精度或没有合格前序也会合法为空。固定身份样本范围是已声明限制，不另作缺陷。 |
| 11 | Python 子进程校验在普通 Go 测试环境中可能跳过：[SAFE 测试](../internal/source/safe/rates_test.go) 找不到 `python3` 时 skip；[Damodaran 国家风险](../internal/ingest/country_risk_test.go)和[行业 Beta 归档测试](../internal/ingest/beta_yield_test.go)未设置 `ALPHALAKE_TEST_PYTHON` 时 skip，即使本机已有 Python。CI 已安装依赖并显式执行相关归档测试，当前不阻塞。 | 维护本地验收入口时提供显式的完整校验模式，缺解释器、依赖或配置须失败，并清楚区分 Go 契约校验与真实解析覆盖；用缺 Python/未配置环境验证严格入口不会静默跳过。 |

完成后删除对应待办，并在相关验收记录写明提交与验证结果；本清单不重复维护已关闭事项。
