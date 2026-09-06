# 税项、利息与债务明细验收

2026-09-06：9 个新增字段、16 个真实金额。大为股份 2025H1、德赛西威 2025 年前三季度复用 `../core-financial-2025/` 的两份完整 PDF；璞泰来 2025 年报 `1224998510.pdf` 随本样本提交，第 78 页合并资产负债表列示应付债券 199,443,189.32 元。没有使用母公司报表或上期金额替代本期值。

TDX 字节复用 `../quarters-2025/` 的 H1/Q3 包与 `../annual-2025/` 年报包。原包 SHA、取得时间及裁剪说明沿用原样本，不构造新观察版本。

`values.csv` 前九列为财报金额及证据位置，另加 `encoded_value` 和 `multiplier`：

- 普通字段以元编码，PDF 金额直接转 float32。
- FN439、FN581 以万元编码，本样本与 PDF 金额除以 10000、按两位小数舍入后的 float32 位一致。标准层乘 10000 后再转 DECIMAL；不声称还原 PDF 的原始分位。
- 例如大为租赁负债 PDF 为 3,097,985.12 元，源值为 float32(309.80) 万元，标准值为 3,097,999.8779296875 元。误差来自源舍入与 float32，不能修改原始证据把它补成财报精度。

```bash
go test ./internal/ingest -run 'TestRealTaxDebtValues|TestRealQuarterFinancialWorkflow|TestRealFinancialWorkflow' -count=1 -v
python3 internal/ingest/testdata/tax-debt-2025/verify.py
```

Python 使用已有 `pypdf` 6.17.0，离线重新提取三份原文指定页的项目行和本期列，并验证舍入口径；文件或依赖缺失直接失败。Go 验证 PDF 哈希、原始位、标准层倍率/单位/期间、PIT 边界和重放。错误倍率导致已有不可信事实删除，修复目录后可重建；FN581 的歧义零值与既有折旧摊销字段一样保留诊断。

数据源目录和 EBIT 使用边界见 [ADR 010](../../../../docs/decisions/010-tax-and-debt-fields.md)。
