# 显式 TTM 与缺失处理

`CompanyValuationInput.prepared_ttm` 可直接接收已完成时点筛选的 TTM，无需把 TTM 放进年度列表。包含 financials、period_start、period_end、带时区的 information_as_of、currency、money_unit（million_reporting_currency）、shares_unit（million_shares）及非空 provenance。报告币种必须一致；禁止与季度旋转同时传入。raw_financials 可为空，也可保留真实年度历史。financials.fiscal_year 仅是容器标签，不触发年度转换。来源系统负责逐字段期间、版本和语义核验，provenance 不代表引擎重新验证了原始证据。

共享编排器直接使用该基期运行 M1–M6。缺少历史资本开支、折旧或营运变动时，相关再投资/FCFF 留空；FCFE 还要求净债务发行和净利润。依赖缺失再投资率的倍数留空。显式零值仍有效。预测可以使用明确假设继续计算，历史缺失不会被预测结果覆盖。

年度加季度入口缺少足够季度时抛出 ValueError；可选流量缺少对应季度分量时返回 null，最新季度余额缺失也不再借用全年余额。股权桥接不完整或股数缺失/非正时不发布每股值；经营价值仍可供单独研究。调用方应向用户展示异常与 warnings；本轮未新增 HTTP 异常响应规范或界面编辑控件。

安克研发费用化、表内租赁已计入时不再次开启相同资本化调整。引擎原有预测政策未改变；AlphaLake 研究样本的额外索偿、转债两路径及投资折价仍在其政策层处理，不伪装成原始报表字段。

验证：backend/tests 全部离线可运行测试及新增 prepared TTM、缺季、缺项、显式零、JSON 往返回归。前端类型同步 nullable 和可选 prepared_ttm；构建结果另与原提交对照，不能把原有错误声明成通过。
