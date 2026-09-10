# AlphaLake 数据到原生估值桥接

本轮已接通：实际 DuckDB → `export-valuation` → `prepared_ttm` → 共享 M1–M6 编排器及显式股权桥接 → HTTP 结果与可复现运行文件。运行代码不读取 `testdata`、CSV 快照或 PDF 样本脚本；样本只用于离线验收和独立复算。

## 当前范围（2026-09-10）

标准导出到共享引擎的CLI/API桥接已扩展到通用非金融政策、行业路由、参考WACC和批量/刷新入口。最新本地5,569家均有处理结论，2,461家条件估值；[全市场验收及运行命令](../../docs/a-share-automation-acceptance-20260910.md)为当前自动化入口。新增[统一公司CLI](../../docs/company-valuation-entry.md)在显式配置范围内展示候选和选择依据；仍不自动发现全部专项模型，也未部署后台周期任务。

本页下列双公司命令、六情景结果及“本轮”限制是2026-09-07初始桥接验收，不是当前全市场能力上限。安克专项政策后来升级为逐年预测；[专项与当前批量口径](../../docs/anker-recalculation-20260909.md)分别保留，不能把不同模型的值串成同口径价格更新。

## 单公司初始桥接用法

在仓库根目录编译并同步已有数据库：

```sh
go build -o alphalake ./cmd/alphalake
./alphalake init ./data/market.duckdb
./alphalake sync-financial ./data/market.duckdb
./alphalake sync-filings ./data/market.duckdb
./alphalake materialize-fundamentals ./data/market.duckdb
```

首次回填需要 `sync-financial --all`，公告按研究日期区间回填；日常默认最新财务包和最近 90 天公告不能替代历史准备。

附注由审核流程提供 JSON 数组，再导入：

```sh
./alphalake import-supplements ./data/market.duckdb ./reviewed-supplements.json
```

每行必须包含 `code`、`period`、`item`、十进制字符串 `value`、`unit`、`period_basis`、`scope`、`announcement_id`、`pdf_sha256`、`pdf_page`、`reviewer`、`review_note`。完整示例在 `internal/ingest/testdata/valuation-integration-2026/supplements.json`，它是固定时期的已核验样本，不能当作新报告的通用补充值。

导入检查数据库已归档的 CNINFO 公告身份、报告期、PDF 哈希及审核声明；这不是重新解析 PDF 或认证审核者身份的服务，数值/行列语义仍须先审核。原审核记录原样留存于 `reviewed_record`，整批原子提交，同内容重放零新增，同原文同项目改值拒绝；目前没有覆盖或撤销审核记录的 CLI。TDX 标准事实不受附注导入影响。

安装已有估值项目依赖并启动服务（示例使用项目虚拟环境）：

```sh
python -m venv .venv
.venv/bin/python -m pip install -e './valuation/backend[dev]'
ALPHALAKE_VALUATION_RUN_DIR="$PWD/data/valuation-runs" \
  .venv/bin/python -m uvicorn api.main:app --app-dir valuation/backend \
  --host 127.0.0.1 --port 8000
```

另一终端从实际数据库发起估值：

```sh
.venv/bin/python valuation/backend/tools/valuate_alphalake.py \
  ./data/market.duckdb 300866 \
  --period 2026-06-30 --as-of 2026-09-06T00:00:00Z \
  --policy valuation/examples/anker-2026H1-central.json \
  > ./data/anker-result.json
```

茅台改用 `600519` 和 `valuation/examples/moutai-2026H1-central.json`。`--alphalake` 可指定二进制，`--api` 可指定服务地址。标准库 CLI 先调用 Go 导出，再 POST `/api/valuation/from-alphalake`；数据库路径不会由请求传给服务器。仅导出可执行：

```sh
./alphalake export-valuation ./data/market.duckdb 300866 \
  --period 2026-06-30 --as-of 2026-09-06T00:00:00Z > ./data/anker-input.json
```

API 请求形状为 `{ "data": <导出 JSON>, "policy": <政策 JSON> }`。API 消费受信任本地调用方提供的导出包，检查来源位、单位、期间、时点、范围和窗口分量；不是对任意外部上传包的数字签名认证。输入快照独立留存，不能把来源哈希误解成服务重新读取了所有原文。

## 分层与计算

- 迁移 026 只增加 `fundamental.reviewed_supplement`。标准字段仍为 80 个 FN；原 158 个研究取值的 TDX 覆盖仍是 124/158，34 个附注补充没有改名为 TDX。
- 导出在一个事务快照内读取当前/上年度标准事实、TTM 窗口及补充。十进制值用字符串交付；未知证券导出空集合。非财务公司补充须与当前时点标准事实使用的公告一致，原 PDF 哈希变化会使旧补充失效。独立财务公司报告沿用独立公告时间，不借母公司时间。
- 适配器将元/股转换为百万元/百万股，转股价不缩放；源 float32 精度不恢复。完整窗口须与源事实、原始位、系数、期间一致。缺失窗口不回退年度值。
- 政策位于 `valuation/examples/` 和估值适配层。安克 EBIT 调整、现金准备、金融投资折价、其他索偿及转债两路径进入原生桥接。茅台以酒业 EBIT 代理、财务公司整块扣除/持有比例加回和经营少数股权比例桥接。
- 共享编排器在 M4 后应用 `EquityBridgeInputs`，再进入 M6；`report.final.value_per_share` 直接返回最终值，不在 API 外拼接。响应保留两种转股情景、桥接分量和最终选择。
- 输入完整度仅统计当前明确政策的必需输入，不代表全部财务数据完整。必需输入缺失返回 HTTP 422 和逐项清单；可选历史资本开支缺失不阻塞显式预测，也不会生成历史 FCFF。

## 结果、重放与边界

每次成功运行保存请求、政策、规范化输入、证据清单、引擎内容摘要、Python/关键依赖版本及输出。运行 ID 由请求、引擎和关键运行时版本决定。同内容重复调用复用同一文件；变更数据或假设形成新 ID 并保留旧结果。文件损坏或相同 ID 对应不同结果时拒绝覆盖，失败请求不写成功记录。默认保存到 `valuation/backend/data/alphalake_runs/`，可用上述环境变量指定目录。

2026-09-07初始桥接实测六情景（元/股，历史政策）：

| 公司 | 审慎 | 中间 | 扩张 |
| --- | ---: | ---: | ---: |
| 安克创新 | 46.292748 | 101.643746 | 205.671436 |
| 贵州茅台 | 733.787457 | 1269.019865 | 1888.816516 |

这些是统一资本周转预测＋已审核桥接政策的条件结果，不是当前目标价。中间结果与既有外部适配实验一致；不能要求与原研究模型的安克 99.21 元、茅台 1267.34 元完全相同，预测税率/利润率/再投资路径本就不同。

该初始政策的验收限制（后续参考WACC和批量扩展见上方当前范围）：

1. 仅两家公司已审核的政策；请求报告期须与政策 `approved_report_period` 一致。本轮审核与验收范围为 2026H1。新公司、新期须审核政策和补充，不能只改日期就宣称适用性已验证。
2. 仅人民币口径、直接假设 WACC；没有自动生成同期无风险利率、风险溢价或市场目标价。宏观容器中的 RF/ERP 在直接 WACC 分支不参与成本估计。
3. 历史完全分类营运资本仍缺，历史 FCFF/FCFE 为空；预测假设不回填历史事实。安克研发费用化，租赁已入账，不再二次资本化。
4. 转债是账面索偿与全部转股两情景取低，额外摊薄是压力假设；员工期权定价叠加被拒绝。茅台财务公司与少数股权是代理分配，不是精确去合并或可分配现金证明。
5. 茅台统一资本倍率没有表达原研究模型的五年储酒、两年长期投入。原 lag=5 被截成 3 的反例仍保留，不能将其包装为相同模型。
6. 尚无新附注自动提取、后台定时调度或前端 AlphaLake 专用页面。本轮完成 CLI/API 原生桥接；新资料更新仍需先同步、审核必要补充，再重跑命令。运行留存不等同于自动资料发现或自动政策审核。

## 验收

后端测试新增真实 Go 生产导入/导出 → FastAPI HTTP 的六情景验证，逐年独立复算预测及原研究桥接分量，检查重放、假设变更、缺项、未来公告、错误单位/来源位/范围、重复输入、未知证券、新报告期及二次资本化/期权叠加。Go 侧验证附注原子失败、冲突拒绝、重开、幂等和独立公告 UTC 16:00 边界。

根 CI 先运行完整标准链与两公司 PDF 重提取，再运行估值后端及双公司适配回归。本轮另启动真实本地 TCP 服务，从验收数据库经 CLI 发起两家公司中间情景调用，不将 ASGI 测试冒称 TCP 验收。验收库由真实归档走生产导入构建，证券身份沿用固定验收输入，未改原有工作库，也不等同于本轮全市场在线同步。

本轮检查通过：`go test ./...`、Go 构建、`go vet ./...`、既有后端 115 项及新增融合 25 项（4 项外部数据测试跳过）、双公司适配默认重放、两公司完整 PDF/模型链及 `git diff --check`。本地 FastAPI/Starlette 发出两条测试客户端弃用提示，不影响结果；未新增依赖种类，安装沿用 valuation 已声明依赖。


## 安克逐年政策 v2

新增 `anker-consolidated-v2`，必须提供十年的 `annual_forecast`（增长、利润率、税率）；旧平坦增长标量不得混入。M4复用共享计算，仅用显式路径替代原默认路径，末年利润率/税率延续至终值。少数股权改为标准TTM FN97×明示倍数。

可选同一WACC债务现值用于股权扣减和转股释放；更新类别股数时必须同时提供募资现金情景及财务/证券条款沿用说明。旧v1政策继续可重放。命令、来源、173.75元对照和158.71—165.79元现金留存情景的边界见[重算说明](../../docs/anker-recalculation-20260909.md)，不应当作完整当前目标价。
