# 生产估值融合验收

`supplements.json` 从已通过原文核验的 `supplement-review-2026/resolved.csv` 中固定原 34 个 CNINFO 补充，包含期间、原文定位、单位、范围和审核说明。原上游 PDF 链在 CI 强制复验；此文件不用于声明新期间或新公司的自动抽取能力。

`TestRealValuationStandardChain` 使用真实归档走生产导入、物化、附注导入和 JSON 导出，验证事务原子性、冲突拒绝、重放、重开及独立公告时点。`ALPHALAKE_VALUATION_EXPORT_DIR` 可将临时验收库关闭后复制到指定目录，同时输出两公司与未知证券 JSON；不打开或修改日常工作库。

`valuation/backend/tests/test_alphalake_integration.py` 默认自行运行该 Go 链，在临时目录生成输入，再通过 HTTP 测试客户端调用正式入口；不从既有 CSV 伪造生产查询结果。CSV 仅作为独立桥接对照。用法与适用边界见 `valuation/docs/alphalake-integration.md`。
