# 核心财务字段真实样本

2026-09-06 验收，复用 2026-09-05 已取得的 TDX 包和 CNINFO 原始 PDF。新增 20 个字段有 35 个非零金额逐位对账：大为股份（002213）2025H1、德赛西威（002920）2025 年前三季度。

`values.csv` 使用与既有财务样本相同的九列格式，保存原文 URL、完整 PDF SHA-256、物理页码和金额。两份完整 PDF 随仓库提交；金额从合并资产负债表、合并现金流量表及现金流量表补充资料的**本期列**读取，没有把母公司报表或上期比较列当成目标值。35 个值覆盖每个新增字段至少一个非零样本。

TDX 字节直接复用 `../quarters-2025/gpcw20250630.zip` 和 `gpcw20250930.zip`，均保留两家公司完整记录；原包与裁剪包哈希、真实取得时间见该目录的 `packages.json`，不再复制 ZIP。原始包金额单位为元，PDF 金额直接转 float32 比较，无万元缩放。

```bash
go test ./internal/ingest -run 'TestRealCoreFinancialValues|TestRealQuarterFinancialWorkflow' -count=1 -v
python3 internal/ingest/testdata/core-financial-2025/verify.py
```

Python 核对使用 `pypdf`（本次已有版本 6.17.0），无网络请求，依赖/文件缺失直接失败。Go 测试强制校验 PDF 哈希并与原始 TDX 位比较；季度流程继续核对标准事实、单位、期间、原始公告、PIT 边界和重放。

本样本明确区分：

- `instant`：期末余额；现金及现金等价物余额也属于存量。
- `H1` / `9M`：年初累计资本开支，不能误标为 Q2 / Q3。
- 折旧摊销：大为 H1 原文有正值；两个公司的 Q1/Q3 源字段均为零，不能从这些零判断是否真实没有折旧，标准层拒绝并记录 `provider_zero_ambiguous`。真实零也会保守留下缺口。
- FN136 不含原文另列的使用权资产折旧；三项折旧摊销不能冒充完整 D&A。

完整字段表、限制和升级步骤见 [ADR 009](../../../../docs/decisions/009-core-financial-fields.md)。
