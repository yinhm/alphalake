# 合并三表批量字段审核

复用安克2025H1、2025FY、2026H1原始PDF及两公司六期TDX裁剪包；本目录不复制PDF或归档，不保存工具实现。

- `evidence.json`：64项通用字段、3份报告、192个当期位置；188个非零匹配，4个横线/源零保留。附页码、主表标题、日期列头、原文当前/比较两列、SHA256、源位、倍率和期间。
- `coverage.csv`：194个源位置的主表候选盘点，含同名的两个贷款位置；64项批准，130项未批准。未定位只是本次主表没有取得合格证据，不声称公司无此业务或TDX无此数据。

```bash
PYTHONPATH=valuation/backend python -m tools.verify_tdx_statement_expansion
go test ./internal/ingest -run '^TestRealStatementExpansion$' -count=1
```

Python依赖既有pypdf，CI已强制执行。去空白前先移除安克主表“七、数字”的附注标记，避免把金额首位吞进附注号；经营/投资/筹资现金净额的“产生/使用”、年度“年初/年末”版式差异用各报告原文标签固定。页面必须属于合并主表，逐表核对单位和当期/比较列头，不允许母公司表混入。

完整范围、命名与期间政策见[ADR019](../../../../docs/decisions/019-complete-statement-field-review.md)。原文重提取、源位一致、标准链与估值窗口分别验证；不将哈希或会计恒等式称作独立取数证据。
