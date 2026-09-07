# 来源与致谢（Credits）

`valuation/` 源自 [chrisuzy/Investment_Valuation_Agent](https://github.com/chrisuzy/Investment_Valuation_Agent)。感谢原作者 **Chirs Yu Zhang**（姓名拼写沿用原 LICENSE）、GitHub 用户 **chrisuzy** 及上游贡献者提供估值引擎、API、前端和相关文档。AlphaLake 在其基础上继续开发，不将上游实现归为本项目原创。

原项目采用 MIT 许可证，完整版权及许可文本保留于 [LICENSE](LICENSE)：

> Copyright (c) 2026 Chirs Yu Zhang

估值方法与 Ginzu 工作簿归功于 **Aswath Damodaran**，原 README 的 [Acknowledgments](README.md#-acknowledgments) 保留。第三方数据集沿用其各自的许可与来源，不因本次目录导入改为 AlphaLake 原创数据。

## 导入记录

| 项目 | 记录 |
| --- | --- |
| 导入日期 | 2026-09-07 |
| 上游仓库 | https://github.com/chrisuzy/Investment_Valuation_Agent |
| 原本地目录 | `workspace/Investment_Valuation_Agent` |
| 本仓库目录 | `valuation/` |
| 此前调研的上游基线 | `c4b86e5`，`feat(ingest): standards-first segment resolver + currency verification` |
| 合并前本地复验版本 | `7ce156a7c6d568d41f480919d84b998bee599d54`，`fix: accept prepared TTM and preserve missing valuation inputs` |
| 用户暂存导入快照的 Git tree | `d0d0a3b6c2adbe58cb69828639342e0627d4d2f3`，416 个文件；不含本轮新增来源说明和 README 导入提示 |
| 导入方式 | 将文件快照纳入 AlphaLake 普通目录；未合并原 Git 提交历史，不是 submodule |

本地复验版本包含此前为 AlphaLake 适配增加的 `prepared_ttm` 和缺失传播修正；不声称该提交已发布到上游。版本信息依据合并前的 Git 检查及既有适配验收记录。导入时原 `.git` 已不在目录中，因此未将当前文件逐字节认证为该旧提交的完整原树；上表的暂存树单独记录实际导入内容。

## 合并边界与验证

AlphaLake 继续负责数据采集、原始证据、标准事实及财务期间派生；本目录承载估值政策、计算和交互应用。目录合并不改变根目录 AGENTS.md 规定的数据分层。

在新目录运行后端测试：**115 passed，4 skipped**。本轮未修改运行逻辑或依赖，未启动 API、未重跑前端构建；此前前端已有构建错误，不宣称本次合并解决。导入快照有既有空白格式问题，未作全目录格式重写。

安克、茅台适配验证脚本仍引用旧 `workspace/Investment_Valuation_Agent` 路径并断言独立仓库 HEAD，后续需要改为本仓库路径及版本校验，再重跑双公司验收。本次是目录导入及来源记录，不代表自动估值集成已经完成。
