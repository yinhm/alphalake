# Review 待办小点

2026-09-30核对后的非主线维护项；优先级见[项目状态](implementation-status.md)。不在这里记录已完成的功能和旧版本升级任务。

本次移除：原第7项的逐次物化诊断累积已由当前宽表实现替代（按源记录/规则持久化，重放不重复写）；原第10项只针对已退出当前模型的迁移021，不再作为当前产品待办。PDF CI覆盖和依赖哈希锁定已实现，不重新列入。依据为当前CI、[物化实现](../internal/store/duckdb/financial_snapshot_materialize.go)及[幂等回归](../internal/store/duckdb/financial_snapshot_test.go)；未运行新的全套测试。

| 编号 | 尚未关闭的范围与证据 | 处理时机及关闭标准 |
| --- | --- | --- |
| 3 | [安克样本 README](../internal/ingest/testdata/anker-valuation-2026/README.md) 已说明跨行标签和租赁负债行截至下一“长期借款”行的边界，但未完整列举 OCF 标签变体等版式特例。 | 下次扩充提取规则时补全“报告/页码、表格、特例原因、限定边界”清单；保持样本专用脚本，不因此建设通用解析器。 |
| 4 | [真实 TTM 测试](../internal/ingest/real_ttm_test.go) 使用 `float64` 精确相等；季度流量另有 PDF 十进制金额和源 float32 的 ULP 界限交叉核对。 | 暂观察。引擎升级或聚合顺序变化时复核；若调整断言，应根据存储精度和计算误差确定界限，不任意放宽金额容差。 |
| 5 | [真实更正样本](../internal/ingest/real_correction_sample_test.go) 未直接断言 `corrects_filing_id` 指向原公告。[存储层测试](../internal/store/duckdb/filing_test.go) 已有该字段断言，故不是完全无覆盖。 | 下次更正样本维护时补实际公告之间的前序 ID 断言，锁定集成链路。 |
| 6 | [模拟端到端测试](../internal/ingest/pit_end_to_end_test.go) 仍以插入/拒绝总数检查物化结果，未直接列出完整预期字段集合；旧文档的候选数量已不适用于当前宽表测试。 | 随测试维护补明确的预期字段集合及拒绝原因；保留计数检查，避免由被测映射目录自动生成期望值而掩盖映射回归。 |
| 9 | [年报工作流测试](../internal/ingest/real_financial_workflow_test.go) 的六份 PDF 正文归档检查依赖 `ALPHALAKE_ACCEPTANCE_RAW`。未设置时会记录日志并跳过，测试不失败；不是毫无提示。 | 单独补自包含验收。区分已固化金额校验与正文归档完整性校验；要关闭此项，六份正文证据须在干净环境可取得并强制校验，不能用 CSV 金额断言冒充 PDF 正文覆盖。 |
| 11 | Python 子进程校验在普通 Go 测试环境中可能跳过：[SAFE 测试](../internal/source/safe/rates_test.go) 找不到 `python3` 时 skip；[Damodaran 国家风险](../internal/ingest/country_risk_test.go)和[行业 Beta 归档测试](../internal/ingest/beta_yield_test.go)未设置 `ALPHALAKE_TEST_PYTHON` 时 skip，即使本机已有 Python。CI 已安装依赖并显式执行相关归档测试，当前不阻塞。 | 维护本地验收入口时提供显式的完整校验模式，缺解释器、依赖或配置须失败，并清楚区分 Go 契约校验与真实解析覆盖；用缺 Python/未配置环境验证严格入口不会静默跳过。 |
| 12 | 简化回溯使用后来下载的TDX历史包，包内修订可能渗入起点输入；[历史复验纪律](valuation/valuation-accuracy.md)已有披露，但“每次复验重新评估影响”尚未形成固定验收项。来源：本轮 `b7634f4..HEAD` review，minor、不阻塞。 | 下次及后续每轮简化回溯，在评分前固定来源版本、首次取得时间、信息截止和拟检查的修订范围；有多个版本时比较输入差异，并在同一政策、同一完整评价分母下报告对预测、误差及采用结论的影响。无法取得旧版本时明确写“影响不可量化”，不能写成无影响；未知修订及拒绝数保留，不按事后误差剔除公司、不要求全量CNINFO补采。将该项接入现有复验协议／验收模板，并以一轮实际记录验证后关闭模板缺口；逐轮重估要求继续保留。 |

| 13 | 全后端10项测试仍消费`current-contract-20260919`冻结三国WACC请求，与当前`country-risk-rating-v2`四国13条契约不符；基线`d7d75a1`也会拒绝，日志在workspace/derived/issuer-reference-integration。 | 单独重建当前契约的回归输入及基线，来源、金额与差异可复验；旧冻结请求按原提交执行，不改写原包、不新增兼容别名、不放宽当前来源范围。闭合后跑相关回归并删除此项。 |

完成后删除对应待办，并在相关验收记录写明提交与验证结果；本清单不重复维护已关闭事项。
