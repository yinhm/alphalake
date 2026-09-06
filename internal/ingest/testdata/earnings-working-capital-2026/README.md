# 七个新增 TDX 字段真实证据

FN12、13、46、47 为期末余额；FN82、83、301 为年初累计损益。全部 CNY 元、倍率 1，2025 年起有效。38 个原文金额的字段号、期间、项目、页码、PDF 路径及 SHA256 在 values.csv；PDF/源包复用相邻两公司样本，不复制原始文件。

完整验收：

```sh
python internal/ingest/testdata/earnings-working-capital-2026/verify.py
```

它进入 valuation-chain 的默认验证：实际生产查询 → 两公司 PDF 全量重新提取 → 本清单/源位核验 → 标准链估值重建。CI 已使用这个父入口，所以新增原文核验同步进入原有 CI，无新依赖。

Go 的 TestRealEarningsWorkingCapitalValues 独立检查 38 个源位和 PDF 哈希；TestRealValuationStandardChain 验证新增字段在真实数据库重开后的标准数值、单位/期间、v21→v22 增量物化、撤销/恢复、重放及 PIT。七字段共新增两公司六期 84 条标准事实；38 是有原文定位的金额数，不把其余季度源值冒充逐项 PDF 核验。

更新必须先审核原文和映射，再按父入口流程重建；直接调用 verify(write=True) 只生成清单，不等于完成原文或生产验收。规则及边界见 ADR 012。
