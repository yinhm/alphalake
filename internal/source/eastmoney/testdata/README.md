# 东财业绩预告真实响应

2026-10-09从东财公开数据中心 `https://datacenter-web.eastmoney.com/api/data/v1/get` 取得，数据集 `RPT_PUBLIC_OP_NEWPREDICT`；原始字节未裁改，非mock。

- `dinglong.json`：证券300054，使用 `(SECURITY_CODE="300054")`。返回3个指标行，包含2026Q3及H1；原count=92，不将片段当完整分页。测试只取明确Q3的两行，锁定归母8.4–8.6亿、扣非7.8–8.0亿，期间从说明的1–9月取得。
- `q3.json`：日期 `(REPORT_DATE='2026-09-30')`，原始5行小页，含每股收益。元/股不能按万元乘；标签未明确基本或稀释EPS。
- `revenue-page.json`：上述报告期、每页100条的真实第2页，66行，count=166；301689收入15.2–16.8亿，与文本152000–168000万元一致，结构化金额已为元。
- `business-error.json`：错误证券引号的真实HTTP200、success=false响应，不当空集。

完整在线分页在workspace验收，单文件样本的count不等于该文件实际行数。单侧、零、负数和冲突篡改在测试中生成，不冒称各类均有真实原文证据。Source指标码只用于解析及核对；业务采用通用名。逐份PDF的独立语义核验不由这些JSON哈希代替。

| 文件 | SHA-256 |
|---|---|
| `business-error.json` | `b793f42493d3dede5ab4fa3225f7cb3a461a6091be0733b208b331d7edfd3745` |
| `dinglong.json` | `32d0486bb80037bfd0145f445293a225dfb8c097975e15386ad710a77b8ebc2f` |
| `q3.json` | `bfda7ddf9f02945815165a705b7ef1805d4120838e1c0a245b15ef9f7ac13327` |
| `revenue-page.json` | `9557cc2353424088a2d888c33294c25d9db0d85c29aec59b01b7076c0023137a` |
