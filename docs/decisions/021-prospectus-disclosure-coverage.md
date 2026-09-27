# ADR 021：招股书提供历史披露范围，TDX提供数值

## 决策

上市前年度不等于未披露，也不等于TDX没有记录。沿用ADR007的来源分层，增加经审核的招股书历史期间关联；不建立PDF财务数值主源，不把招股书伪装成多份年度报告。

- CNINFO目录保存真实公告身份、正文及披露日期。`prospectus`没有凭标题猜出的报告期，也不直接成为定期报告锚点。
- `fundamental.filing_coverage_review`保存不可变审核事件及其原始审核文件。每条审核明确公司、公告、一个年度、标准字段集合、PDF哈希、页码、审核者与说明；不保存替代金额。`active_filing_coverage`只采用未被显式替代/撤销、哈希仍匹配的审核。
- 当前只允许审核年度累计字段。标准映射规则仍来自统一目录；没有审核覆盖的字段不参与该招股书锚点物化。该限制不是TDX解析白名单。
- 常规定期报告优先；只有缺少合格定期报告时才使用招股书覆盖。同等级同日期的候选仍拒绝歧义。公告必须不晚于TDX版本首次取得，年度必须早于披露；日期精度仍按中国次日零点可用。
- 标准值直接从既有TDX不可变ZIP重建，保留float32精度、标准名称和来源定位。源零、缺项、单位/范围冲突不因新增披露关联而自动转为事实。
- 审核发布、替代或撤销后运行`materialize-fundamentals`，同一流程刷新关联并按内容签名重算；撤销删除不再有依据的值，恢复后从TDX重建。无关归档保持原内容签名，重放不重复写事实。每条审核原子提交，批量失败明确报告已完成项，不冒称整批完成。

## 入口与证据

```sh
# 对schema52副本执行一次；先保存备份，重开及数值比较通过后发布。
./alphalake upgrade-filing-coverage workspace/alphalake.duckdb

# codes.txt每行一个六位证券代码；单进程串行复用数据库连接。
./alphalake sync-filings workspace/alphalake.duckdb --prospectus \
  --codes-file workspace/derived/prelisting-rd-20260927/codes.txt \
  --start 2019-01-01 --end 2025-12-31 --window-days 366
./alphalake export-prospectuses workspace/alphalake.duckdb
./alphalake import-filing-coverage workspace/alphalake.duckdb workspace/derived/prelisting-rd-20260927/verified/reviews.json
./alphalake materialize-fundamentals workspace/alphalake.duckdb
```

`action=publish`新增审核；替代或`action=revoke`必须提供当前审核的`supersedes`哈希，禁止覆盖历史。审核不回填一个不存在的年度公告。副本操作显式设置`ALPHALAKE_WORKSPACE`指向归档根目录。

CNINFO定向检索用已解析的`stock=证券,组织ID`、关键词“招股”、空分类，再严格区分正文、摘要、附录和通知。真实接口对完整关键词“招股说明书”会漏报；德冠新材的原始返回已固化在`internal/source/cninfo/testdata/prospectus-catalogue/`。不同检索模式使用独立检查点；抓取保留分页完整性及正文验证。

`valuation/backend/tools/verify_prospectus_coverage.py`使用已安装的`pdftotext`离线核对人工标定的页码、表头、期间列、金额及源精度。允许原文两位小数舍入加TDX半ULP误差，保留差异；输出给入库端的审核记录不含任何金额。PDF原件、人工审核清单、核对台账和派生输出全部在workspace，代码与小型离线回归留在仓库。核对数值不替代人工对合并范围及年度列的确认。

历史财报后来被重新整理的结构性前视偏差仍保留；本功能没有证明下载的TDX版本就是IPO当时已保存的版本，也不提供历史系统时点查询。
