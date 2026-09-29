# 标准财务事实导出 SQLite

日常更新、原生API验收与原子发布使用[系统交付入口](system-delivery.md)。本文保留底层独立导出命令和字段契约；单独导出成功不等于网页已切换或估值已验收。

当前提供TDX财务及版本化Damodaran参考组成的SQLite快照，完整报表目标仍有缺口；经用户批准，现金及长期投资的已知组成允许作为显式代理用于估值；安克、苏泊尔另通过[估值输入代理](../valuation/valuation-book-proxies.md)接通原生API。专用前端和估值旁路已撤除；原页面和请求契约已恢复，未获准或不适用代理的缺项仍在后端拒绝，详见[实际模型准入](../valuation/valuation-input-gates-20260925.md)。工具不修改源DuckDB或默认seed。逐期间已核实TDX余额源零可通过[独立审核补充](../history/historical-debt-zero-review.md)进入目标输入，保留完整审核证据，不写入标准事实；旧v7快照须显式重导出。[本轮重新接入](../history/valuation-reconnect-20260926.md)已补债务组成、历史研发与市场价格代理；现金/长期投资列以`estimated_partial_scope`写入已知分量并允许估值使用，不能直接视作完整目标总额。当前证据快照契约为`alphalake-sqlite-v8`；新导出不再将报表利润总额误填为剔除特殊项目税前利润，旧快照不能据旧available标记冒充语义已审核。

依赖Python 3.11+标准库和当前版本`alphalake`程序，无新增包。先构建当前程序；本机的大库任务、构建和测试须串行放在独立systemd系统服务中，参见[内存隔离约束](fundamental-memory-20260919.md)。例如：

```bash
cd /root/alphalake
systemd-run --unit=alphalake-sqlite-build \
  --property=WorkingDirectory=/root/alphalake \
  --property=MemoryMax=1G --property=MemoryHigh=896M \
  --property=MemorySwapMax=0 --property=OOMPolicy=stop \
  --setenv=GOPATH=/root/go --setenv=GOCACHE=/root/.cache/go-build \
  --setenv=GOMEMLIMIT=128MiB --setenv=GOMAXPROCS=1 \
  /usr/bin/go build -p 1 -o /tmp/alphalake-sqlite-export ./cmd/alphalake
journalctl -u alphalake-sqlite-build --no-pager
systemctl show alphalake-sqlite-build -p ActiveState -p ExecMainStatus
```

确认构建退出码为0后导出（更换输出路径；命令拒绝覆盖已有文件）：

```bash
systemd-run --unit=alphalake-sqlite-export \
  --property=WorkingDirectory=/root/alphalake \
  --property=MemoryMax=1G --property=MemoryHigh=896M \
  --property=MemorySwapMax=0 --property=OOMPolicy=stop \
  --setenv=PYTHONPATH=valuation/backend \
  --setenv=ALPHALAKE_DUCKDB_MEMORY_LIMIT=1GiB \
  --setenv=ALPHALAKE_DUCKDB_THREADS=1 \
  --setenv=GOMEMLIMIT=128MiB --setenv=GOMAXPROCS=1 \
  /usr/bin/python3 -m tools.export_alphalake_sqlite \
  --database workspace/alphalake.duckdb \
  --output workspace/derived/valuation.next.sqlite \
  --period 2026-06-30 --as-of 2026-09-25T00:00:00Z \
  --code 300866 --code 600519 --code 002032 \
  --alphalake /tmp/alphalake-sqlite-export
systemctl show alphalake-sqlite-export -p ControlGroup -p MemoryMax -p ActiveState -p ExecMainStatus
journalctl -u alphalake-sqlite-export --no-pager
```

`--code`可重复，或显式换成`--all`选择本地全部沪深A股证券分母；身份拒绝保存在`export_universe`。默认10个年度、8个季度，可用`--years 1..10`和`--quarters 1..8`缩短。不存在的指定证券拒绝；不根据缺数据静默缩短历史。源库须关闭写入并完成checkpoint，无WAL；逐次检查文件状态，发布前复核完整SHA256，源发生变化则不发布。结果在同目录临时文件完整写入并检查后原子创建；运行失败不留下最终SQLite。

## 数据范围与语义

链路：TDX原始包→已有标准语义/校验→DuckDB标准宽表→SQLite→网页→共享估值引擎。CNINFO关联只提供身份、时点与核验血缘；PDF附注金额、参考市场数据和模型假设不进入快照。

SQLite的`standard_facts`保存原生模型已映射目标及现金、投资、债务组成所需的标准事实、单位及独立证据，`export_cells`逐单元格保存缺项和差分血缘。全部346字段由主库标准查询提供；不再复制整份三表、TTM和估值JSON。导出通过`export-financial-snapshot`在一次只读事务内批量投影目标列，Python逐证券写入SQLite，不逐公司/期间启动进程。

仍提供`companies`、`financials_annual`、`financials_quarterly`供通用数据读取；这些宽表金额为**百万元人民币**、股数为**百万股**，`standard_facts`金额仍为元、股数为股，按各行单位解释。原生估值使用宽表。保留源精度，不补小数。日期锚点来自指定报告期，年表以最近完整自然年为FY0，季表以指定季末为FQ0。固定时点取数不是逐季度当时留存的数据版本认证。

| SQLite列 | 标准字段 | 转换 |
|---|---|---|
| revenues | revenue_cumulative | 累计流量 |
| ebit | reported_ebit | 来源报告EBIT累计值，不冒称调整后经营EBIT |
| ebitda | reported_ebitda | 来源报告EBITDA累计值 |
| net_income | net_income_parent_ytd | 归母净利润累计流量 |
| interest_expense | interest_expense | 累计流量 |
| capex | capital_expenditure_cash | 购建长期资产现金累计流量，非净再投资 |
| total_tax_expense | income_tax_expense | 累计流量 |
| r_and_d_expense | research_and_development_expense | 累计流量 |
| bv_equity | equity_parent | 期末归母权益 |
| shares_outstanding | total_shares | 期末股本 |
| minority_interests | noncontrolling_interests | 期末少数股东权益 |
| bv_debt | 五项标准债务组成 | 五项齐全才合计，范围警告见重新接入记录 |
| cash_and_marketable_securities | cash_and_cash_equivalents及资产组成 | 现金等价物金额写入并标记范围未闭合的代理；不假定已覆盖短期投资 |
| cross_holdings | 长期股权、债权、其他债权、其他权益工具及其他非流动金融资产 | 可用分量逐项校验后求小计；缺项不补零，已知小计作为显式账面代理 |
| companies.effective_tax_rate | income_tax_expense / profit_before_tax | 最近完整年度会计有效税率，非预测税率；缺项、分母≤0或比例不在[0,1]时留空并诊断 |
| companies.mv_equity | 合格收盘价×total_shares | 报告期市值代理；已知B/H股时按用户授权采用A股价格×总股本，标记a_share_total_share_proxy，不是分股类真实市值 |

年度流量取全年累计；单季流量为当年累计减前一季度累计，Q1直接使用。不跨年度差分、不加总期末余额；前期缺失则差分结果为空。追溯调整可能影响跨期可比性，本工具未独立审核比较口径。来源冲突期间留空；字段单位、期间、身份或范围不符合预期则拒绝整份导出。

总折旧摊销、租赁费用以及剔除特殊项目税前利润仍无已审核目标映射，保留NULL。现金及证券合计、长期投资已接入部分组成，目标范围未闭合时写入已知代理值并保留警告；全缺失仍为NULL；债务采用五项账面组成合计，具体边界见[重新接入记录](../history/valuation-reconnect-20260926.md)。EBIT/EBITDA现从来源报告标准指标导出，[组成边界](../history/tdx-supplementary-fields-20260925.md)明确保留，不因填列而批准原生估值。行情读取本地合格TDX日线，缺失时不编造；已提供最近完整年度的会计有效税率，但信用评级、预测税率假设、地理分部、期权、租赁承诺、行业与WACC政策不编造。补充证据不自动合并进标准事实。

长期投资诊断现复用三表推导的五项标准组成，`available_component_million_cny`为全部已知组成小计，`missing_components`只列该组合缺项；即使算术组成齐全，也不自动认证可全额加回的估值范围。详见[接入及真实验收](../history/tdx-asset-derivation-20260926.md#长期投资组成接入正式导出)。

`export_cells`逐单元格记录`available`、`missing_standard_fact`、`source_record_conflict`、`estimated_partial_scope`或`requires_separate_valuation_definition`等状态，保留标准值、系数、事实ID、可用时间和归档哈希。标准事实缺失不进一步推断为源零、未披露或未审核；更细状态需查`financial-statements`。`metadata`记录源库/程序/导出器哈希、报告期、信息截止及取得时间；公司`data_as_of`为信息截止日期，不冒充实际下载日。

## 年度有效税率

会计有效税率沿用原页面“Latest Annual”口径，以最近完整年度的所得税费用除以利润总额；证据记录两个标准事实及比例。它不是现金税率，也不是边际税率或未来正常化税率；达摩达兰区分这些概念，预测仍需显式选择：[税率讨论](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/taxrate.htm)。异常比例不截断成0或1，不把利润总额填入“剔除特殊项目税前利润”。

## 网页消费与估值

当前8080使用[100家公司验收快照](../history/valuation-100-market-20260926.md)：100家已导出；招股披露补链后87家准入、13家保留缺项，结果见[最新发布记录](../history/valuation-prelisting-rd-20260927.md)。此前市值代理阶段的72/28仅为历史验收。

如需手动让网页读取该快照，在启动网页后端的环境中显式设置：

```bash
export US_CN_HK_DB_PATH=/root/alphalake/workspace/derived/valuation.sqlite
```

仅改变该进程选库，不合并到seed。原页面搜索与选择公司后，原客户端提交`{ticker, risk_free_rate}`；不上传政策文件、不新增参数表单。需要修改参数时沿用原页面。安克原始财务列仍有两项资产范围缺口；后端使用获准代理及配套经营收益后，原请求返回200。苏泊尔已补市值并原生计算成功，茅台市值也已补齐，现已允许通用资产代理并通过准入；不将样本成功扩称全市场准入。

构建前端后启动后端：

```bash
PYTHONPATH=valuation/backend .venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8080
```

只读诊断（结构化输出仅供后端/API，不要求前端理解）：

```bash
curl http://127.0.0.1:8080/api/database/compatibility/SZSE:300866
```

返回公司/期间、年度季度34项列覆盖、28个公司快照字段存在性，以及`required_missing`、`conditional_missing`、`optional_history_missing`和警告。[准入仅检查当前实际消费的输入](../valuation/valuation-input-gates-20260925.md)，不以默认十年/八季窗口作为门槛；状态为`ready`、`blocked_required_inputs`或`blocked_reference_inputs`。覆盖不等于语义已审核。原普通`POST /api/valuation`和PATCH保留，不再绑定专用只读会话；安克已通过获准政策下的原生计算验收；不代表纯披露事实已完整。

此网页进程只读SQLite，不打开大DuckDB。大库任务继续使用独立systemd服务和1GiB限额。接口恢复不代表认可原默认无风险利率、行业回退等参数适用于A股；显式自动假设候选已实现但未替换网页默认，具体范围见[规格](../history/valuation-native-integration-spec-20260924.md)。

## 原生参考发布

当前v8在财务表之外包含`reference_release`、`reference_value`、`reference_company`；参考来自同一DuckDB的已完成发布，归档哈希须合格。主库须当前schema55，缺必要发布即拒绝导出，不从workspace散落工作簿回退。默认US行业方法保留，详见[参考接入与边界](../valuation/native-reference-bridge.md)。

默认参考截止等于`--as-of`；`--reference-as-of <RFC3339>`允许明确使用另一参考截止。例如固定旧财务窗口比较最新参考时，两个截止分别记录，不能称当时可用的历史估值。旧快照须显式重新导出，不能只改metadata契约标签。

`/api/database/compatibility/<ticker>`分别返回财务状态和参考缺项。财务齐备但缺少默认行业/国家参考时为`blocked_reference_inputs`；这不是TDX财务缺失。用户可在既有`from-database` API传`industry_override`及新增的`country_override`明确选择会话参考，选择留痕、不回写公司分类；原前端无改动。

缺项诊断会遍历全部目标分量：`export_cells.evidence_json`对缺标准值记录`kind=missing_standard_fact`及字段/期间/系数，后续已有金额和血缘仍保留。必需分量不齐时金额仍为NULL，不能把已知分量小计当成完整债务；源零原因须另回查来源证据。真实核对见[近期债务缺口](../history/native-capital-proxy-review.md#2024年债务缺口已定位)。
