# AlphaLake

以达摩达兰方法为基础，为沪深普通非金融企业提供可追溯的条件估值。主要链路：**TDX → DuckDB标准事实 → SQLite发布快照 → 原生valuation网页/API**。当前没有生产部署；金融专项模型、多市场扩展和长期历史回测不是主线。

## 从这里开始

| 需要做什么 | 文档 |
|---|---|
| 看当前能力、缺口和下一步 | [项目状态](docs/implementation-status.md) |
| 找操作、接口或方法说明 | [文档索引](docs/README.md) |
| 理解数据库与数据分层 | [架构](docs/design.md) |
| 更新数据并发布网页快照 | [同步、验收与发布](docs/guides/system-delivery.md) |
| 独立导出SQLite、核对输入 | [导出契约](docs/guides/valuation-sqlite-export.md) |
| 用Agent估值、查询与比较 | [估值Skill](skills/alphalake-valuation/SKILL.md) |
| 核对自动假设与资本效率 | [估值方法](docs/valuation/README.md) |

主库为`workspace/alphalake.duckdb`；网页使用`workspace/derived/valuation.sqlite`。TDX是主要结构化数值来源，其有效公告日期直接采用；CNINFO补充披露身份、时点和原文核验。缺公告关联或日期不阻断当前数值，达摩达兰参考数据与财务合库。SQLite可以重建，不能作为第二权威库。文件位置与清理约定见[workspace布局](docs/guides/workspace-layout.md)。

固定100家公司用于发布回归；证券分母、准入数量、时点及限制统一在[项目状态](docs/implementation-status.md)维护。样本成功率不等于全市场完成度，更不等于估值准确率。网页保留原流程，用户可修改参数；自动候选尚未替换默认政策。

## 开发与验证

Go版本以`go.mod`为准，Python依赖见`valuation/backend/pyproject.toml`。常用检查：

```bash
go test ./...
go build ./cmd/alphalake
PYTHONPATH=valuation/backend .venv/bin/python -m pytest -q valuation/backend/tests
```

**本机全套测试、大库同步和导出必须串行运行于已核验MemoryMax的独立systemd服务**，不能直接把这些命令并行启动。具体环境及限制见[资源隔离](docs/guides/fundamental-memory-20260919.md)。只改文档时检查链接和`git diff --check`，无需重复全套测试。

日常后端开发使用该指南中的快速范围并补相关回归；完整组保留真实Go/PDF证据和研究回放，默认pytest与CI仍全量执行，不要求每次局部修改都重复完整组。

本地只读状态：`./alphalake status workspace/alphalake.duckdb`。命令参数以当前程序帮助为准；日常发布使用上表入口，不照抄历史验收中的旧路径和版本。

## 来源与致谢

估值应用[`valuation/`](valuation/README.md)源自[chrisuzy/Investment_Valuation_Agent](https://github.com/chrisuzy/Investment_Valuation_Agent)。感谢原作者Chirs Yu Zhang及贡献者。导入版本、MIT许可证与Credits见[来源记录](valuation/UPSTREAM.md)。项目继续复用其共享引擎和原页面。
