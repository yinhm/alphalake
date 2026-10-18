# 开发测试与全局验收

默认测试只承担当前运行时的日常回归；重型真实来源和跨语言验收在明确全局测试或CI中执行。已结束研究的结果对账按原提交复验，不将冻结历史强行迁到当前契约。

## 运行入口

在项目根目录执行以下命令；本机全套命令须按[资源隔离](fundamental-memory-20260919.md)串行放到独立1GiB服务，不直接并行启动。

| 范围 | Go | Python后端 |
|---|---|---|
| 日常默认 | `go test -timeout 30m ./...` | `PYTHONPATH=valuation/backend .venv/bin/python -m pytest -q valuation/backend/tests` |
| 修改对应文件/功能 | 加`-run`或限定包 | 限定测试文件或`-k` |
| 显式全局 | `go test -tags=full -timeout 30m ./...` | 上述pytest命令加`--full --durations=30` |
| 重型链路局部验收 | `go test -tags=full ./internal/ingest -run '^TestRealValuationStandardChain$' -count=1` | `pytest --full tests/test_alphalake_integration.py`（在backend目录） |

Go的`full`构建标签才包含重型测试；`-run`不代替标签，遗漏标签可能找不到测试却退出0。Python的`full`标记在默认收集后明确列为deselected，不执行夹具；`--full`或`CI=true/1`纳入。CI两边都显式使用全局命令，并在安装Python依赖后运行Go，避免首次skip后逐组重复执行。无新增测试依赖。

测试取消`ALPHALAKE_WORKSPACE`覆盖；显式设置`GOPATH/GOCACHE`，跨语言验收设置`ALPHALAKE_TEST_PYTHON`。本地默认测试勿继承`CI=true`。结果区分通过、跳过和未选择，不把默认组称全局通过。

研究阅读链的轻量回归为`pytest valuation/backend/tests/test_research_reports.py`及前端`npx playwright test tests/research.spec.ts`；后者覆盖无需公司加载、表格／公式、附件、搜索筛选与移动端。现有`npm run test:knowledge`执行全部前端浏览器测试，包含research与原估值页面回归。新增`remark-gfm`只为研究Markdown正确呈现表格，不变更KB或估值计算。

## 保留范围

- 默认组保留引擎公式与错误输入、当前财务累计/季度/TTM及单位转换、研发队列、假设与信用边界、SQLite/API准入、发布回滚、知识接口，以及Go核心存储/同步和源解析。小型合成测试和轻量真实解析仍执行。
- 全局组保留TDX→建库→导出→HTTP→引擎的真实链、映射失效与审核撤销、真实参考发布、PDF/工作簿批量取证及现行适配证据。篡改拒绝不删减，不用模拟测试宣称真实源可用。
- 涉及财务解析、映射、导出或参考绑定的修改，除默认组外须执行相应真实链；广泛接口变更或明确全局请求执行全局。纯文档只查链接和`git diff --check`。

研究清理按“是否维护当前产品行为”审核：已完成的历史回测、旧实验模型/阈值、训练及bootstrap冻结结果、一次性旧契约转换可退出当前测试集。当前仍消费的现金诊断证据元数据、公式/缺项、研究统计小型算法及原始字段语义检查保留；不因路径含research就整批删除。

本轮删除清单、原提交、测量及失败记录见[清理验收](../history/test-suite-audit-20261003.md)。源证据、研究失败结论和工具未删除；复验冻结历史时检出其原提交，不要求当前运行时支持旧版本。
