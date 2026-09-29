# AlphaLake 网页

TDX SQLite快照的使用、数据边界及重建命令见[导出与网页估值](../../docs/guides/valuation-sqlite-export.md)。已恢复原页面/导航和原数据库请求，移除专用政策UI与TDX结果页。当前TDX快照未通过原生数据契约，API明确报告阻断；原模型的手工输入与交互保留。

构建：`npm ci`后执行`npm run build`，CI执行同样检查。当前前端无新增依赖。

浏览器验收脚本为`tests/tdx-sqlite.smoke.cjs`。它启动临时本地后端并在结束后关闭；先构建前端，准备含安克与茅台的v2 SQLite，再在独立内存受限服务中运行`node valuation/frontend/tests/tdx-sqlite.smoke.cjs`（从仓库根目录）。需要：

- `US_CN_HK_DB_PATH`：待验收SQLite绝对路径。
- `ALPHALAKE_TEST_PYTHON`：已安装后端依赖的Python解释器。
- `ALPHALAKE_PLAYWRIGHT_MODULE`：已有`playwright-core`模块的绝对路径；未设置则按Node正常模块查找。
- `ALPHALAKE_CHROMIUM_PATH`：已安装Chromium程序路径；未设置则使用Playwright已安装浏览器。
- 可选`ALPHALAKE_SCREENSHOT`：截图保存路径。

检查三家真实快照的原请求格式、无需政策UI、数据不足拒绝；另用原内置示例验证原页面及导航。示例已修复四季度不足以旋转H1 TTM的问题，使用完整年度输入；演示成功不冒称TDX接入完成。浏览器依赖不属于应用运行依赖；本地已运行此检查，CI当前覆盖Python链路与前端构建，不声称CI已运行浏览器。
