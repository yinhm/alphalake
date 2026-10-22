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

选股链的轻量回归为`pytest valuation/backend/tests/test_valuation_results.py`及前端`npx playwright test tests/screen.spec.ts`，覆盖冻结研究投影、零／负值／未知、范围与分母、数值筛选单位、稳定分页、ETag、篡改拒绝及发布失败保留指针。浏览器检查URL还原、报告返回、关键假设、手机与键盘；无需财务数据库或重估。实际发布再逐页核对选定完整批次的API结果及性能，不能以合成测试替代真实全范围验收。

选股导入另以1,000份独立公司元数据验证全部访问后可及时释放，防止附件目录随证券数无限驻留；该断言检查对象存活数量，不将合成测试耗时当成全市场性能。实际5,227行恢复导入及失败过程见[研究交付记录](../history/company-story-recoverage-20261021.md)。

宏观阅读链的轻量回归为`pytest valuation/backend/tests/test_macro_pages.py`及前端`npx playwright test tests/macro.spec.ts`，覆盖点评证据绑定、不可变期次、原子登记、零／负值／缺项、图表尺度、历史入口及窄屏。该Python文件加`--full`时额外串行执行17组真实原文的Go建库→消费导出→发布→API验收；临时workspace独立，不读取真实主库。宏观页面按需加载图表代码，浏览器回归同时保留原估值、KB及research检查。

发布比较还覆盖新增覆盖／统计期／历史、零与未知、数值与来源修订、区间／定义变更、ID重排及Decimal精度；宏观与研究互链变化同时执行`tests/research.spec.ts`，包含非法本地链接拒绝。全负数柱图也须保留零基线。比较及阅读请求不得启动Go或估值计算。

## 保留范围

- 默认组保留引擎公式与错误输入、当前财务累计/季度/TTM及单位转换、研发队列、假设与信用边界、SQLite/API准入、发布回滚、知识接口，以及Go核心存储/同步和源解析。小型合成测试和轻量真实解析仍执行。
- 全局组保留TDX→建库→导出→HTTP→引擎的真实链、映射失效与审核撤销、真实参考发布、PDF/工作簿批量取证及现行适配证据。篡改拒绝不删减，不用模拟测试宣称真实源可用。
- 涉及财务解析、映射、导出或参考绑定的修改，除默认组外须执行相应真实链；广泛接口变更或明确全局请求执行全局。纯文档只查链接和`git diff --check`。

研究清理按“是否维护当前产品行为”审核：已完成的历史回测、旧实验模型/阈值、训练及bootstrap冻结结果、一次性旧契约转换可退出当前测试集。当前仍消费的现金诊断证据元数据、公式/缺项、研究统计小型算法及原始字段语义检查保留；不因路径含research就整批删除。

本轮删除清单、原提交、测量及失败记录见[清理验收](../history/test-suite-audit-20261003.md)。源证据、研究失败结论和工具未删除；复验冻结历史时检出其原提交，不要求当前运行时支持旧版本。
