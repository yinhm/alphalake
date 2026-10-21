# AlphaLake 网页

原生估值保留既有编辑流程；`/research`阅读公司与专题报告，`/screen`统一覆盖总览，另有`/macro`和`/knowledge`。当前范围及数据状态只查[项目状态](../../docs/implementation-status.md)，SQLite/API见[数据契约](../../docs/valuation/data-contract.md)。

在本目录执行`npm ci`、`npm run dev`。`npm run build`只生成`workspace/derived/frontend-candidate`及Vite依赖清单，**不会替换正在服务的网页**。从项目根目录发布已验收的候选：

```bash
PYTHONPATH=valuation/backend .venv/bin/python -m tools.publish_frontend
```

构建、测试及发布按[隔离与发布约定](../../docs/guides/system-delivery.md#前端构建与发布)执行。发布器先验证全部静态／动态依赖，补齐散列资源后原子替换HTML；保留已发布资源供旧页面继续切页，禁止整目录替换后删除旧资源。回滚HTML及发布收据在`workspace/derived/frontend-publication`。

HTML不缓存，散列资源长期缓存，文本响应gzip压缩。读取研究／宏观／选股不加载未使用的估值页面；页面资源加载失败由用户明确刷新，不自动重载并丢弃手工输入。

浏览器回归用`npx playwright test`，期间冻结前端源码、样式和本目录文档，避免Vite／Tailwind热更新刷新测试中的页面。真实数据验收使用[数据发布手册](../../docs/guides/system-delivery.md)；合成测试通过不代替真实SQLite及研究目录验收。前端构建不增加依赖，也不改变估值公式或假设。早期v2 SQLite烟测说明已随契约演进退出当前操作入口，冻结验收仍按原提交复验。
