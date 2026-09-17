# 安克2024期初关联方借款补证

从既有2024年年报原件找到先前未接入的分类证据，不重新下载：CNINFO公告1223379891，2025-04-29披露；首次取得2026-09-11，URL/哈希/原取得记录保留在[回执](acceptance.json)。旧2026-09-06研究台账不回写，当前资本审核改用本次补证后的缺项状态。

| 原文项目 | 页码 | 2024-12-31金额，元 |
| --- | ---: | ---: |
| 关联方借款总额 | 155、158 | 26,560,607.17 |
| 对应坏账准备 | 158 | 2,656,060.72 |
| 借款净额（总额−准备） | 派生 | 23,904,546.45 |
| 其他应付款“其他”，仍未分类 | 174 | 59,852,820.39 |

第155页说明借款来自处置子公司，且全项借款总额等于第158页前五名中客户1余额，故客户1的坏账准备覆盖该项借款，不误用其他应收款全部准备或上年比较列。该证据支持从经营资本分类中单列这笔处置形成的应收款，不证明可全部现金收回。第174页剩余混合项目继续保留，不归零。

使用既有`import-supplements`在`workspace/supor-reviewed-assets-20260917/acceptance.duckdb`主库副本导入3项，重放新增0；通过`export-valuation`按2024-12-31、信息截止2026-09-17T16:50:00Z导出并冻结[完整结果](export.json.gz)。57条标准事实、16个窗口与导入前一致，原主库未写入。补充依赖已有已归档、已解析公告，不制造标准事实或替代TDX总额。

另从原文核验其他应收款净额126,612,165.92元，编码与已有2024 TDX FN13源位一致；**原schema39映射从2025开始，旧导出仍保留该缺口；迁移040现将FN13有效期扩至已核验的2024-12-31**，不扩展更早季度。已有库须迁移并重新物化才能新增旧期事实。采用真实裁剪gpcw、原CNINFO目录/PDF的新隔离链完成迁移、撤销删除、恢复、重放及重开导出，详情见[升级回执](schema40-acceptance.json)和[标准导出](schema40-export.json.gz)。新链3项补充由生产入口重新导入，Python适配器读取FN13标准窗口126,612,168元，不恢复PDF小数。两个验收库范围分别保留，不冒称主库已经发布；剩余缺口是其他混合项目及完整资本分类。完整经营资本、净再投资、历史FCFF和公司倍率继续为空。

```bash
./alphalake import-supplements <已有原文的db> valuation/research/company-inputs-20260917/anker-opening-2024/supplements.json
PYTHONPATH=valuation/backend python -m tools.review_anker_capital_evidence
PYTHONPATH=valuation/backend python -m pytest -q valuation/backend/tests/test_company_input_review.py
```

校验连接完整原文哈希、页/标签/本期列、金额、TDX源位与实际补充导出；将准备增加0.01元必须拒绝。纳入现有backend pytest，无新增依赖。公司资本审核回执同步更新，旧期原文新取证不冒称2026-09-06已知。

原文PDF复用仓库中[已有完整年报](../../../../internal/ingest/testdata/anker-cash-history-2024/1223379891.pdf)，已删除本目录逐字节相同的重复副本。新增Go真实链测试为`TestRealAnkerReceivablesHistory`，Python回归同时验证升级前缺项与升级后标准窗口，不将源位匹配单独当作物化完成证据。
