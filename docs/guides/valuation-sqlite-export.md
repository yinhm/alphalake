# 标准财务事实导出 SQLite

当前按[消费快照spec](../valuation/sqlite-input-snapshot-spec.md)实现v13：DuckDB负责完整标准事实与治理，SQLite交付原生财务宽表、TTM、参考和紧凑上下文。切换状态及全范围验收见[项目状态](../implementation-status.md)。旧快照必须从主库重建，不能直接改版本号。

当前提供TDX标准财务及版本化Damodaran参考组成的SQLite快照，保持原生valuation页面、请求和估值引擎。经用户批准，现金及长期投资的已知组成可作为显式代理用于估值；不按公司套专项EBIT政策。财务值、代理与真实缺项分别留痕，准入依据见[实际模型准入](../valuation/valuation-input-gates-20260925.md)。工具不修改源DuckDB或默认seed。当前代码契约为`alphalake-sqlite-v13`，旧快照须显式重导出；主库与网页是否已切换以[项目状态](../implementation-status.md)为准。

逐期间已核实TDX余额源零可通过[独立审核补充](../history/historical-debt-zero-review.md)进入目标输入，保留完整审核证据，不写入标准事实；旧快照须显式重导出。[本轮重新接入](../history/valuation-reconnect-20260926.md)已补债务组成、历史研发与市场价格代理；现金/长期投资列以`estimated_partial_scope`写入已知分量并允许估值使用，不能直接视作完整目标总额。当前证据快照契约为`alphalake-sqlite-v13`；新导出不再将报表利润总额误填为剔除特殊项目税前利润，旧快照不能据旧available标记冒充语义已审核。

依赖Python 3.11+标准库和当前版本`alphalake`程序，无新增包。先构建当前程序；本机的大库任务、构建和测试须串行放在独立systemd系统服务中，参见[内存隔离约束](fundamental-memory-20260919.md)。例如：

```bash
ALPHALAKE_REPO="$PWD"  # 在项目根目录执行
systemd-run --unit=alphalake-sqlite-build \
  --property=WorkingDirectory="$ALPHALAKE_REPO" \
  --property=MemoryMax=1G --property=MemoryHigh=896M \
  --property=MemorySwapMax=0 --property=OOMPolicy=stop \
  --setenv=GOPATH="$(go env GOPATH)" --setenv=GOCACHE="$(go env GOCACHE)" \
  --setenv=GOMEMLIMIT=512MiB --setenv=GOMAXPROCS=1 \
  /usr/bin/go build -p 1 -o /tmp/alphalake-sqlite-export ./cmd/alphalake
journalctl -u alphalake-sqlite-build --no-pager
systemctl show alphalake-sqlite-build -p ActiveState -p ExecMainStatus
```

确认构建退出码为0后导出（更换输出路径；命令拒绝覆盖已有文件）：

```bash
systemd-run --unit=alphalake-sqlite-export \
  --property=WorkingDirectory="$ALPHALAKE_REPO" \
  --property=MemoryMax=1G --property=MemoryHigh=896M \
  --property=MemorySwapMax=0 --property=OOMPolicy=stop \
  --setenv=PYTHONPATH=valuation/backend \
  --setenv=GOPATH="$(go env GOPATH)" --setenv=GOCACHE="$(go env GOCACHE)" \
  --setenv=ALPHALAKE_DUCKDB_MEMORY_LIMIT=1GiB \
  --setenv=ALPHALAKE_DUCKDB_THREADS=1 \
  --setenv=GOMEMLIMIT=512MiB --setenv=GOMAXPROCS=1 \
  /usr/bin/python3 -m tools.export_alphalake_sqlite \
  --database workspace/alphalake.duckdb \
  --output workspace/derived/system-delivery/valuation.sqlite.next \
  --period 2026-06-30 --as-of 2026-09-25T00:00:00Z \
  --code 300866 --code 600519 --code 002032 \
  --alphalake /tmp/alphalake-sqlite-export
systemctl show alphalake-sqlite-export -p ControlGroup -p MemoryMax -p ActiveState -p ExecMainStatus
journalctl -u alphalake-sqlite-export --no-pager
```

`--code`可重复，或显式换成`--all`选择本地全部沪深A股证券分母；身份拒绝保存在`export_universe`。默认10个年度、8个季度，可用`--years 1..10`和`--quarters 1..8`缩短。不存在的指定证券拒绝；不根据缺数据静默缩短历史。源库须关闭写入并完成checkpoint，无WAL；逐次检查文件状态，发布前复核完整SHA256，源发生变化则不发布。结果在同目录临时文件完整写入并检查后原子创建；运行失败不留下最终SQLite。

## 数据范围与语义

链路：TDX原始包→已有标准语义/校验→DuckDB标准宽表→SQLite→网页→共享估值引擎。CNINFO补充公告身份、时点与核验血缘，不作为当前金额准入前提；PDF不覆盖TDX，参考数据和模型假设不混入标准财务事实。

SQLite不创建`standard_facts`、`standard_records`、`export_cells`、`reviewed_source_zeros`或`export_universe`。全部标准字段、精确金额、来源和审核由主库提供。`export-financial-snapshot`在单次只读事务内批量投影实际目标字段及期间；Python批量写宽行，不逐公司启动进程。

内部中间文件为`records.jsonl`宽行与`fields.jsonl`定义，另有证券、冲突和审核补充。只在导出临时目录使用，不是网页运行依赖。源记录元数据每报告输出一次；金额以十进制字符串交付，NULL和0保留。`--field-periods`提供标准字段到真实期间数组的JSON，导出器按年度展示、季度差分及当前/上年TTM组成限定读取；不因最早年度边界读取其间所有旧季度。

`companies`、`financials_annual`、`financials_quarterly`及`financials_ttm`金额为**百万元人民币**、股数为**百万股**。主库标准金额仍为元/股；详细核对须按源单位查询，不从SQLite REAL恢复源精度。原生估值使用宽表，非年末从已有prepared TTM入口直通，无需凑齐季度。日期锚点为指定报告期，FY0为最近完整自然年，FQ0为指定季末；展示窗口不缩短，缺历史不补零。详见[数据接入spec](../valuation/data-contract.md)。

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
| companies.mv_equity | 合格收盘价×total_shares | 最新完整收盘价×报告期股本代理；已知B/H股时按用户授权采用A股价格×总股本，标记a_share_total_share_proxy，不是分股类真实市值 |

TTM流量按上一全年＋本年累计−上年同月累计直接计算，三个组成独立留痕，不要求Q1或四季齐全；年末直接取全年。资产余额始终取当前期末。年度流量取全年累计；单季流量为当年累计减前一季度累计，Q1直接使用。不跨年度差分、不加总期末余额；前期缺失则差分结果为空。追溯调整可能影响跨期可比性，本工具未独立审核比较口径。来源冲突期间留空；字段单位、期间、身份或范围不符合预期则拒绝整份导出。

总折旧摊销、租赁费用以及剔除特殊项目税前利润仍无已审核目标映射，保留NULL。现金及证券合计、长期投资已接入部分组成，目标范围未闭合时写入已知代理值并保留警告；全缺失仍为NULL；债务采用五项账面组成合计，具体边界见[重新接入记录](../history/valuation-reconnect-20260926.md)。EBIT/EBITDA现从来源报告标准指标导出，[组成边界](../history/tdx-supplementary-fields-20260925.md)明确保留，不因填列而批准原生估值。行情读取信息截止时最近已完成交易日的本地合格TDX未复权收盘价，窗口为最近完成收盘日起14个自然日；价格不以财报期末截断，取得运行须始于对应交易日15:00或之后。市值代理采用该价格×已披露报告期总股本，股本基期与价格日期分别留痕，不能视作当前完整分股类市值。缺行情时不编造；已提供最近完整年度的会计有效税率，但信用评级、预测税率假设、地理分部、期权、租赁承诺、行业与WACC政策不编造。补充证据不自动合并进标准事实。

长期投资诊断现复用三表推导的五项标准组成，`available_component_million_cny`为全部已知组成小计，`missing_components`只列该组合缺项；即使算术组成齐全，也不自动认证可全额加回的估值范围。详见[接入及真实验收](../history/tdx-asset-derivation-20260926.md#长期投资组成接入正式导出)。

准入状态由宽表值、固定映射规则及`metadata.input_context:<ticker>`的稀疏例外重建。保留来源冲突、部分必需组成缺失、负投资拒绝和有效审核引用；不逐格保存available行或代理的完整资产诊断。现金/投资代理仍标为`estimated_partial_scope`并提示范围未闭合；完整分量与证据显式查数据侧。总股本非正时保留原值和报价，市值NULL并标记`nonpositive_reported_shares`。单位、身份、非有限值不放行；NULL不推断为源零。

## SQLite证据存储契约

v13每证券一条紧凑上下文，记录身份、实际源记录ID、市场/税率状态、例外及审核哈希、累计TTM所需观察与上年同窗收入。源包哈希共享在全库元数据；公司输入与上下文共同签名，篡改值、单位、期间或审核状态拒绝。完整源金额/公告/PDF/审核包不复制到SQLite。后端诊断标明`evidence_expanded=false`，来源引用不等于完整证据。

全库元数据须含`contract=alphalake-sqlite-v13`、`input_context_format=native-wide-context-v2`、财务内容签名、投影规范及范围收据哈希。冻结候选身份和完整分母放`workspace/derived/sqlite-export/`，不参与网页运行。运行端只支持当前版本；旧证据用原提交复验，代码与快照协调切换，成功重开后清理回滚副本。

## 年度有效税率

会计有效税率沿用原页面“Latest Annual”口径，以最近完整年度的所得税费用除以利润总额；证据记录两个标准事实及比例。它不是现金税率，也不是边际税率或未来正常化税率；达摩达兰区分这些概念，预测仍需显式选择：[税率讨论](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/taxrate.htm)。异常比例不截断成0或1，不把利润总额填入“剔除特殊项目税前利润”。

## 网页消费与估值

8080使用完整沪深发布范围；固定100家公司仅作回归样本，实际分母及准入见[项目状态](../implementation-status.md)。历史样本数量不能代替现行全范围状态。

如需手动让网页读取该快照，在启动网页后端的环境中显式设置：

```bash
export US_CN_HK_DB_PATH="$PWD/workspace/valuation.sqlite"
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

当前v13另含五张既有参考表：`reference_release`、`reference_value`、`reference_company`、`reference_issuer_association`和`reference_wacc`。原生参考契约v4，从当前主库schema58已完成的发布读取；缺包或错误哈希拒绝，不读散落工作簿回退。原US行业口径、发行人关联、人民币来源初值及用户覆盖规则保持。见[参考边界](../valuation/native-reference-bridge.md)。

默认参考截止等于`--as-of`；`--reference-as-of <RFC3339>`允许明确使用另一参考截止。例如固定旧财务窗口比较最新参考时，两个截止分别记录，不能称当时可用的历史估值。旧快照须显式重新导出，不能只改metadata契约标签。

`/api/database/compatibility/<ticker>`分别返回财务状态和参考缺项。财务齐备但缺少默认行业/国家参考时为`blocked_reference_inputs`；这不是TDX财务缺失。用户可在既有`from-database` API传`industry_override`及新增的`country_override`明确选择会话参考，选择留痕、不回写公司分类；原前端无改动。

原生国家参考解析范围为CN/HK/US/IL；Israel用于消费既有沪深公司名单中的国家条目，不扩采以色列证券或改变国家选择政策。国家税率与ERP必须覆盖同一声明范围，缺任一项拒绝发布；范围随SQLite内容签名绑定会话。国家标签仍是来源分类，不证明公司的实际地区风险暴露。较早的显式人民币WACC政策国家权重仍限CN/HK/US，本轮不扩展该政策接口。

国家风险源数据集现为`country-risk-rating-v2`，13条记录（四国各三项加成熟市场ERP）；当前显式WACC参考输入须从主库重新导出，不改写历史冻结请求或保留旧数据集别名。既有归档可通过`sync-country-risk --local <xlsx>`重新解析注册，之后`--offline`幂等重放；采集时点与文件内业务日期分别保留。

必需组成不齐仍为NULL，不把已知小计当完整债务；部分已有分量的缺项引用保存在稀疏例外，源零及全缺项原因用`financial-statements`查主库。详细研究可调用`tools.export_alphalake_sqlite.query_standard_inputs(connection, field_periods, source_database=None)`，`field_periods`为标准名称→所需报告期列表：一次批量投影，余额只读期末、流量明确累计窗口，不为新增字段扩大其他字段的期间。核对SQLite绑定的财务内容签名；主库只因无关预告变动而物理哈希变化时重新核对财务投影，否则拒绝补旧输入。实际源金额和治理始终留在数据侧。收益／资产组成及消费值对账使用[通用审阅入口](../valuation/valuation-book-proxies.md#通用数据对账入口)，不复制进网页数据库。


原生API的TTM、年度及展示季度依据出口的`annual_bv_equity=parent_attributable`元数据，将同期期末`bv_equity + minority_interests`映射为模型的`consolidated_book_equity`（合并账面权益），年度与季度规则相同；无需新增SQLite冗余列。来源范围不明确或任一分量缺失时不构造该值。资本诊断使用此字段，股权桥接中的少数权益扣减独立保存；不得将后者的市场估计反用于账面资本。未提供合并范围的外部输入仍可按其他已齐备输入计算DCF，但相应资本诊断留空。

## 财务数值与公告日期

当前v10消费现有标准财务快照，`financial_time_basis=current_standard_snapshot_not_pit`。`--as-of`仍用于身份、行情观测及默认参考截止，不再是财务公告准入门槛；财报期仍限定财务窗口。已知TDX公告日期优先，CNINFO可补充；未知日期留空，不丢弃有效金额。日期精度按中国次日零点记录保守边界，不能解释为真实时分秒。不同数据包版本按来源取得顺序选择，保留源哈希；不声称严格历史回放。依据和迁移见[ADR022](../decisions/022-financial-availability-and-disclosure.md)。

投资代理遇到负分量时，该单元格保留`negative_investment_component_requires_review`及源值证据，代理值留空；标准源负值和其他公司仍导出，不对异常值取绝对值或归零。
