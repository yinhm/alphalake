# 现金流量补充资料标准审核

复用安克2025H1、2025FY、2026H1三份原PDF及现有TDX裁剪包，不重复保存原始文件。

`evidence.json`核对12项、36个当期非零金额和36个比较列金额。页码为148、184–185、138，属于合并财务报表附注的“59、现金流量表补充资料”；当期/上期列和元单位均有断言。年度表跨页，半年报括号全半角不同，逐报告保留原文标签。源零仍拒绝，比较列不写成本期事实。

`remaining-review.csv`是schema49的305个已命名但未标准审核源位置的后续处置清单，使用通用字段名；末列仅用于源维护定位。它不含122个未知位置，也不宣称305项均应成为可加总的财务事实。两处同名贷款仍分别保留。

```bash
PYTHONPATH=valuation/backend python -m tools.verify_tdx_statement_expansion internal/ingest/testdata/cashflow-reconciliation-2026
go test ./internal/ingest -run '^TestRealCashflowReconciliation$' -count=1
```

复用三表校验器及整批标准链测试；既有Python3/pypdf依赖，默认CI强制原文验证。负向检查覆盖金额、位、倍率、期间、章节、符号和虚假零。源码位比较只证明编码一致，章节/行标签与期间的审核才限定业务语义。范围与落库结果见[本轮说明](../../../../docs/cashflow-reconciliation-review-20260919.md)。
