# AlphaLake

以达摩达兰方法为基础，为沪深普通非金融企业提供可追溯的条件估值。主要链路：**TDX → DuckDB标准事实 → SQLite发布快照 → 原生valuation网页/API**。已将全量本地沪深数据发布到现有网页，独立生产部署目标待明确；具体交付进度见项目状态；金融专项模型、多市场扩展和长期历史回测不是主线。

## 从这里开始

| 需要做什么 | 文档 |
|---|---|
| 看当前能力、缺口和下一步 | [项目状态](docs/implementation-status.md) |
| 找操作、接口或方法说明 | [文档索引](docs/README.md) |
| 理解数据库与数据分层 | [架构](docs/design.md) |
| 更新数据并发布网页快照 | [同步、验收与发布](docs/guides/system-delivery.md) |
| 独立导出SQLite、核对输入 | [导出契约](docs/guides/valuation-sqlite-export.md) |
| 用Agent检查数据、手工刷新与发布 | [数据维护Skill](skills/alphalake/SKILL.md) |
| 用Agent估值、查询与比较 | [估值Skill](skills/alphalake-valuation/SKILL.md) |
| 复用数据、独立DCF与交叉校验 | [独立DCF Skill](skills/alphalake-dcf/SKILL.md) |
| 核对自动假设与资本效率 | [估值方法](docs/valuation/README.md) |

主库为`workspace/alphalake.duckdb`；网页使用`workspace/valuation.sqlite`。TDX是主要结构化数值来源，其有效公告日期直接采用；CNINFO补充披露身份、时点和原文核验。缺公告关联或日期不阻断当前数值，达摩达兰参考数据与财务合库。SQLite v12只保存原生财务宽表、TTM、参考和紧凑上下文；完整标准事实、审核和来源归DuckDB/归档，研究显式查数据侧。SQLite可以重建，不能作为第二权威库。文件位置与清理约定见[workspace布局](docs/guides/workspace-layout.md)。

固定100家公司用于发布回归；证券分母、准入数量、时点及限制统一在[项目状态](docs/implementation-status.md)维护。样本成功率不等于全市场完成度，更不等于估值准确率。网页保留原流程，用户可修改参数；数据库新会话按已批准的[来源初值](docs/valuation/default-assumptions.md)起步并保留用户覆盖；没有经济参数自动胜出者，公司信用、经营预测等经济依据仍须核验。

## 开发与验证

Go版本以`go.mod`为准，Python依赖见`valuation/backend/pyproject.toml`。常用检查：

```bash
go test -timeout 30m ./...
go build ./cmd/alphalake
PYTHONPATH=valuation/backend .venv/bin/python -m pytest -q valuation/backend/tests
```

**本机全套测试、大库同步和导出必须串行运行于已核验MemoryMax的独立systemd服务**，不能直接把这些命令并行启动。具体环境及限制见[资源隔离](docs/guides/fundamental-memory-20260919.md)。只改文档时检查链接和`git diff --check`，无需重复全套测试。

以上默认命令执行日常回归。真实Go建库、PDF全量取证及跨语言验收仅在显式全局测试或CI运行：Go加`-tags=full`，Python加`--full`；涉及这些链路的修改须补相应全局回归。已结束研究的冻结结果按原提交复验，不再跟随当前运行时反复重放。范围及删除记录见[测试约定](docs/guides/testing.md)。

本地只读状态：`./alphalake status workspace/alphalake.duckdb`。财务、行情及参考更新并发布网页统一使用`tools.publish_native_valuation --published-scope`，在锁内冻结当前网页完整范围；在线模式另须明确最近已完成交易日`--quote-date`，具体命令见[发布手册](docs/guides/system-delivery.md)。业绩预告沿[独立入口](docs/guides/earnings-guidance.md)入主库，不自动发布进财务SQLite。命令参数以当前程序帮助为准，不照抄历史验收中的旧路径和版本。

Codex日常维护可用`$alphalake`，原生公司估值／历史查询用`$alphalake-valuation`；`$alphalake-dcf`只复用数据，独立建模与交叉校验，缺事实允许定向官方PDF补证，交付中文报告及公式Excel。Skill源码随本仓库维护；本地首次安装可在项目根目录执行：

```bash
ALPHALAKE_SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills"
mkdir -p "$ALPHALAKE_SKILL_DIR"
for ALPHALAKE_SKILL_NAME in alphalake alphalake-valuation alphalake-dcf; do
  ln -s "$PWD/skills/$ALPHALAKE_SKILL_NAME" "$ALPHALAKE_SKILL_DIR/$ALPHALAKE_SKILL_NAME"
done
```

已有同名安装先核对来源，不覆盖其他Skill；可只链接所需目录。软链接让仓库更新同步生效，安装后下一轮对话可发现新Skill。独立DCF不调用现有估值引擎或原生导出；其公式Excel执行核验须使用实际可用的Linux计算工具，`openpyxl`本身不能重算。研究补证和结果不自动进入主库或网页发布。

## 来源与致谢

估值应用[`valuation/`](valuation/README.md)源自[chrisuzy/Investment_Valuation_Agent](https://github.com/chrisuzy/Investment_Valuation_Agent)。感谢原作者Chirs Yu Zhang及贡献者。导入版本、MIT许可证与Credits见[来源记录](valuation/UPSTREAM.md)。项目继续复用其共享引擎和原页面。

中文[`alphalake-dcf`](skills/alphalake-dcf/SKILL.md)改写自Anthropic的`financial-services/dcf-model`，来源版本、改写范围及Apache-2.0许可证随Skill保留。
