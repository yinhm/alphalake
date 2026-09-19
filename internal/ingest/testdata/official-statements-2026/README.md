# 官方三表批量映射：代表性单位与精度证据

三表全量映射的定义依据是已冻结的两份官方目录及单位说明。本目录只做代表性原文抽检，不将3字段样本冒充新增124项逐公司PDF审核。

`evidence.json`复用安克2025H1、2025FY、2026H1三个原始PDF及已有TDX裁剪包。9个当期取值和9个比较列：基本每股收益、稀释每股收益、现金期初余额。金额规范为元，每股收益规范为元/股；期初余额的报告期和实际余额日分开。

原文两种每股收益均有四位小数，但本样本中基本每股收益按四位、稀释每股收益按两位编码再进入float32。例如2026H1稀释每股收益原文3.1270，TDX约3.13；库保留源值，不按PDF恢复小数。`pdf_decimals`仅控制样本版式匹配，`encoding_decimals`仅用于证明该样本源编码，不写成全市场舍入规则。固定小数位匹配防止去空白后相邻两列粘连误分。

复用现有离线校验器，显式CI步骤执行；金额、单位、期间与两种错误精度均有拒绝测试。

```bash
PYTHONPATH=valuation/backend python -m tools.verify_tdx_statement_expansion internal/ingest/testdata/official-statements-2026
go test ./internal/ingest -run '^TestRealOfficialStatements$' -count=1
```

Go真实标准链另外覆盖全部124个新映射在两公司六期包中的非零/零分支、规范三表查询、错误倍率撤除与恢复、重开重放。[设计与发布](../../../../docs/decisions/020-official-statements-and-snapshots.md)区分全目录定义审核、源编码检查及抽样PDF证据。
