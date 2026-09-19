# 完整源目录的冻结依据

取得日期2026-09-19，网页发布日期未知。原始文件按gzip压缩保存，未改内容；URL、字节数、SHA256及portfolio提交见`official-financial-fields.receipt.json`。

- `official-financial-fields.html.gz`：通达信官方专业财务字段说明，438行含一项“暂无”；Go测试独立读取HTML并逐项核对目录原描述。
- `official-financial-unit-rules.html.gz`：官方未标明金额/股数单位及缺失展示规则；不以缺失展示零推断真实会计零。
- `portfolio-types.py.txt.gz`：本地portfolio参考词典，原作者 **Copyright 2021 yinhm**，提交`a16e6fc`；原版权声明随文件保留。仅补官方本页遗漏的25项说明，不执行其代码，不引入numpy，不把它当独立财报审核。

完整解析目录不等于标准事实批准。真实584位置回归复用`internal/ingest/testdata/valuation-chain-2026`裁剪包；后续未定义位置继续保留float32位。实现及接入范围见[全量目录说明](../../../../../docs/tdx-financial-catalog-20260919.md)。
