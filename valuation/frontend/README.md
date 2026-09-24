# AlphaLake 网页

TDX SQLite快照的使用、数据边界及重建命令见[导出与网页估值](../../docs/valuation-sqlite-export.md)。网页消费标准事实/TTM及独立显式政策，展示条件结果与三表状态；原外部数据页面保留独立来源。

构建：`npm ci`后执行`npm run build`，CI执行同样检查。当前前端无新增依赖。

浏览器验收脚本为`tests/tdx-sqlite.smoke.cjs`。它启动临时本地后端并在结束后关闭；先构建前端，准备含安克与茅台的v2 SQLite，再在独立内存受限服务中运行`node valuation/frontend/tests/tdx-sqlite.smoke.cjs`（从仓库根目录）。需要：

- `US_CN_HK_DB_PATH`：待验收SQLite绝对路径。
- `ALPHALAKE_TEST_PYTHON`：已安装后端依赖的Python解释器。
- `ALPHALAKE_PLAYWRIGHT_MODULE`：已有`playwright-core`模块的绝对路径；未设置则按Node正常模块查找。
- `ALPHALAKE_CHROMIUM_PATH`：已安装Chromium程序路径；未设置则使用Playwright已安装浏览器。
- 可选`ALPHALAKE_SCREENSHOT`：截图保存路径。

检查搜索、无需文件的默认参数、手工修改参数的实际提交、无效终值参数拒绝、结果及三表展示、金融兼营拒绝和浏览器运行异常。浏览器依赖不属于应用运行依赖；本地已运行此检查，CI当前覆盖Python链路与前端构建，不声称CI已运行浏览器。
