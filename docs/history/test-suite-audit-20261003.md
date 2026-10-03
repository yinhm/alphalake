# 测试耗时审核与历史研究清理

2026-10-03；清理前提交`47bd8ba`。用户明确要求删除过期研究测试，将必要重型验收改为显式全局或CI执行。当前命令以[测试约定](../guides/testing.md)为准。

## 原因与处置

此前完整后端675通过/4跳过、662.01秒；Go采集包769.325秒、存储包132.690秒。同日已有日志在`workspace/derived/cny-reference-bridge/`，不是本轮重新测量的基准。9月30日JUnit表明研究回放、真实Go导出和PDF重复提取占主要时间；默认pytest仍全部执行，上一版快速范围依赖人工指定，容易随新增文件失效。

删除下面34个测试文件，以及2个独立历史复验函数，共107个测试函数（参数展开前）。这些测试对账已结束的增长/利润/现金/资本开支/训练和不确定性研究，或修补旧契约字段以比较冻结报告，不维护当前原生估值交付。工具、原始PDF/ZIP、冻结结果与失败结论未删除；复验用`git show 47bd8ba:<路径>`或检出原研究提交，不保留当前运行时旧兼容。

| 删除文件（均位于`valuation/backend/tests/`） | 函数数 |
|---|---:|
| `test_tdx_history.py` | 38 |
| `test_temporal_forecasts.py` | 4 |
| `test_tdx_cash_revenue.py` | 1 |
| `test_tdx_cash_scope.py` | 1 |
| `test_tdx_equal_weight.py` | 2 |
| `test_tdx_error_bands.py` | 1 |
| `test_tdx_gradual_margin.py` | 2 |
| `test_tdx_growth_calibration.py` | 2 |
| `test_tdx_half_growth.py` | 4 |
| `test_tdx_half_growth_scope.py` | 5 |
| `test_tdx_loss_recovery.py` | 2 |
| `test_tdx_multiyear_growth.py` | 1 |
| `test_tdx_normalized_margin.py` | 2 |
| `test_tdx_normalized_scope.py` | 2 |
| `test_tdx_ocf_scope_uncertainty.py` | 1 |
| `test_tdx_ocf_uncertainty.py` | 2 |
| `test_tdx_origin_inputs.py` | 1 |
| `test_tdx_profit_uncertainty.py` | 2 |
| `test_tdx_q3_calibration.py` | 3 |
| `test_tdx_q3_training_scope.py` | 2 |
| `test_tdx_rd_growth.py` | 1 |
| `test_tdx_rd_profit.py` | 2 |
| `test_tdx_revenue_cagr.py` | 2 |
| `test_tdx_revenue_selection.py` | 2 |
| `test_tdx_trade_balances.py` | 1 |
| `test_tdx_training_earnings.py` | 2 |
| `test_tdx_training_exclusions.py` | 3 |
| `test_tdx_ttm_yoy.py` | 2 |
| `test_tdx_zero_calibration.py` | 3 |
| `test_tdx_zero_growth.py` | 2 |
| `test_tdx_zero_horizons.py` | 3 |
| `test_standard_history_five.py` | 1 |
| `test_standard_history_forecast.py` | 2 |
| `test_current_contract_evidence.py` | 1 |

另外删除`test_fundgr_semantics.py::test_historical_global_tables_and_dates`及`test_alphalake_cash.py::test_failed_cash_replication_is_bound_to_actual_result`。当期增长表/表头、现金核对公式与缺项、证据元数据不可变、小型研究统计及研发真实语义反例仍保留，没有按research路径一刀切。

19个Go真实建库文件增加`full`标签；21个Python取证/实链文件及7个单独用例标记`full`。现有默认核心存储、同步/缓存拒绝、源解析、引擎、原生API输入、条件假设、发布回滚和知识接口仍执行。源范围、缺项及篡改边界未删；默认排除是deselected，不冒称skip或全局通过。Python新增5种选择状态的回归，防止显式全局或CI漏跑。

共享Go财务、WACC及市值导出夹具归入conftest并改为session，只执行一次，后续用例读取同批来源。CI在Python依赖安装后一次执行Go全局，不再先缺依赖skip再逐组重跑；后端显式`--full`。两个公司适配原始脚本仍实际执行完整来源链，不加模拟后备。

## 验证与测量

动态证据在`workspace/derived/test-suite-audit/`：清理前逐文件哈希与函数清单、删除原因、分层范围、Go JSON事件、Python JUnit及逐用例耗时。比较须区分删除过期工作量、默认排除和保留工作量的实际速度；不把缩小默认范围称同范围引擎提速。当前主库、网页发布、引擎和估值默认均未改。

首次默认Go编译发现季度真实链文件中声明的`quarterSampleDir`被两个轻量解析测试共用；移到默认样本帮助文件。冻结字段分母帮助函数也移到既有默认fixture，函数体保持原样。轻量季度/累计原文位核对也移到默认样本文件，测试内容不变。首次失败日志保留；默认和full分别编译，避免某一构建范围独自通过。

| 范围 | 结果 | 单次耗时 |
|---|---|---:|
| 默认Python | 418通过、4既有跳过、155未选择 | 25.77秒 |
| 保留的Python全局 | 573通过、4既有跳过 | 346.19秒 |
| 默认Go整仓库（强制重跑） | 通过；移回默认的季度核对另做专项通过 | 225.13秒 |
| Go全局（强制重跑） | 281个顶层测试通过 | 946.70秒 |

默认Go采集58.49秒、存储129.28秒；全局采集771.90秒，与上轮769.33秒接近，说明重型验收工作量仍保留。默认Python2条、全局11条既有警告；4项跳过仍是上游TEST_DATA文件缺失。默认执行＋全局执行覆盖全部当前测试：没有失去保留的359个函数，新增1个函数的5种参数化选择状态。

同机、暖编译缓存、Go使用`-count=1`强制重跑、每次pytest是独立进程，串行1GiB服务；不是多次中位数或冷缓存基准。阶段工作量不同，不将默认与旧完整组直接解释为同范围算法性能提升。构建、默认及full vet通过；季度源位校验两种范围通过。远端CI尚未运行，本地仅验工作流语法及对应全局命令。

正式`valuation-chain-2026/verify.py`按新full调用通过，两公司生产标准链、PDF/显式补充、模型差异及F10负向验证均通过；未用“找不到Go测试但退出0”替代验收。默认服务组峰值602,816,512字节、全局服务组峰值894,070,784字节，均在1GiB硬上限内；未提高限额、并行启动或修改引擎。全文链接及`git diff --check`通过。
