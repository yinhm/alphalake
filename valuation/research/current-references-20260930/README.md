# 四国参考契约的当前方法回归输入

仅重建安克创新300866、苏泊尔002032两份方法回归请求，不复制其余历史包。旧`current-contract-20260919`及原研究证据均原样保留；旧三国请求仍应被当前四国契约拒绝。

## 来源及变化

- 用已有官方`internal/source/damodaran/testdata/ctrypremJuly26.xlsx`和当前严格解析器重新读取13项国家参考；Israel三项分别来自`ERPs by country!D78/E78/F78`，不填默认值。
- 原10项的金额、原始值、源单元格、日期及观测标识不变；只改其发布绑定，新增3项Israel观测。工作簿哈希及原首次取得时间保留。
- 重建国家发布的dataset、解析版本、归一化签名和content key；使用新的夹具内release ID，`recorded_at`记本次重建时间。其他发布及全部财务、政策、预测、资本效率、时点和证据内容不变。
- 原信息截止保持2026-09-10，`recorded_cutoff=null`；新解释是在9月30日重建的，**不是9月10日已发布的历史快照或严格PIT验收**。本目录不代表真实数据库发布，局部ID仅供独立请求的关联校验。
- 两家公司仍选择原国家暴露和原WACC政策，新增Israel行不参与其成本计算。未改引擎、公式或任何默认参数。输入重建不继承旧run ID，不把可计算称为经济合理性认证。

`receipt.json`记录源包和新包哈希、工作簿和解析器哈希、实际解析运行时及重建时间。已有源10项不变由全量逐项比较保证；当前回归继续复核DCF算术、资产边界、增量重估和缺项拒绝。

## 可重复生成

在仓库根目录、装好`valuation/backend[dev]`依赖后，输出到不存在的临时目录：

```bash
PYTHONPATH=valuation/backend python -m tools.rebuild_method_closure_references \
  /tmp/alphalake-current-reference-rebuild \
  --recorded-at 2026-09-30T09:16:30Z
```

同一Python/openpyxl运行时、同一输入和重建时间生成相同字节。运行时改变会如实改变解析签名及新包哈希，不能沿用原签名。新一次重建使用真实的新时间，核验后显式更新调用路径；工具拒绝覆盖既有目录，不在应用加载时自动转换或兼容旧包。

轻量回归：`test_current_reference_evidence.py`核验完整来源/金额不变性、可重复重建、旧scope拒绝、Israel缺项/重复及raw值篡改拒绝；`test_method_closure.py`、`test_incremental_valuation.py`、`test_company_input_review.py`消费本目录两份请求。其他不含WACC绑定的资产证据继续使用原财务v2输入。
