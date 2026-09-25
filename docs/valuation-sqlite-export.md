# 标准财务事实导出 SQLite

当前提供纯TDX来源的SQLite财务快照，**尚未达到原 Investment_Valuation_Agent SQL/API兼容目标**。专用前端和估值旁路已撤除；原页面和请求契约已恢复，当前快照在后端明确拒绝原生估值，详见[字段契约及待审批缺口](valuation-native-data-contract-20260924.md)。工具不修改源DuckDB或默认seed。当前证据快照契约仍为`alphalake-sqlite-v2`；新导出不再将报表利润总额误填为剔除特殊项目税前利润，旧快照不能据旧available标记冒充语义已审核。

依赖Python 3.11+标准库和当前版本`alphalake`程序，无新增包。先构建当前程序；本机的大库任务、构建和测试须串行放在独立systemd系统服务中，参见[内存隔离约束](fundamental-memory-20260919.md)。例如：

```bash
cd /root/alphalake
systemd-run --unit=alphalake-sqlite-build \
  --property=WorkingDirectory=/root/alphalake \
  --property=MemoryMax=1G --property=MemoryHigh=896M \
  --property=MemorySwapMax=0 --property=OOMPolicy=stop \
  --setenv=GOPATH=/srv/gopath --setenv=GOCACHE=/root/.cache/go-build \
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
  --setenv=ALPHALAKE_DUCKDB_MEMORY_LIMIT=256MiB \
  --setenv=ALPHALAKE_DUCKDB_THREADS=1 \
  --setenv=GOMEMLIMIT=128MiB --setenv=GOMAXPROCS=1 \
  /usr/bin/python3 -m tools.export_alphalake_sqlite \
  --database workspace/auto-valuation-20260909/market.duckdb \
  --output workspace/sqlite-export-new/valuation.sqlite \
  --period 2026-06-30 --as-of 2026-09-22T00:00:00Z \
  --code 300866 --code 600519 --code 002032 \
  --alphalake /tmp/alphalake-sqlite-export
systemctl show alphalake-sqlite-export -p ControlGroup -p MemoryMax -p ActiveState -p ExecMainStatus
journalctl -u alphalake-sqlite-export --no-pager
```

`--code`可重复，或显式换成`--all`选择准入查询的全部A股证券分母；身份拒绝保存在`export_universe`。默认10个年度、8个季度，可用`--years 1..10`和`--quarters 1..8`缩短。不存在的指定证券拒绝；不根据缺数据静默缩短历史。源库须关闭写入并完成checkpoint，无WAL；逐次检查文件状态，发布前复核完整SHA256，源发生变化则不发布。结果在同目录临时文件完整写入并检查后原子创建；运行失败不留下最终SQLite。

## 数据范围与语义

链路：TDX原始包→已有标准语义/校验→DuckDB标准事实及TTM→SQLite→网页→共享估值引擎。CNINFO关联只提供身份、时点与核验血缘；PDF附注金额、参考市场数据和模型假设不进入快照。

SQLite增加`standard_facts`保存所有查询所得的标准字段、原标准单位及独立证据，`financial_statements`保存指定期间的完整三表与缺项状态，`valuation_inputs`保存当前标准事实/TTM及源位核验血缘（附注列表明确为空）。不再按网页17列限制标准数据供给。

仍提供`companies`、`financials_annual`、`financials_quarterly`供通用数据读取；这些宽表金额为**百万元人民币**、股数为**百万股**，标准长表与三表金额仍为元、股数为股，按各行单位解释。这些额外载荷仅保留证据；原生估值必须使用宽表，不再绕过宽表读取专项载荷。保留源精度，不补小数。日期锚点来自指定报告期，年表以最近完整自然年为FY0，季表以指定季末为FQ0。固定时点取数不是逐季度当时留存的数据版本认证。

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

年度流量取全年累计；单季流量为当年累计减前一季度累计，Q1直接使用。不跨年度差分、不加总期末余额；前期缺失则差分结果为空。追溯调整可能影响跨期可比性，本工具未独立审核比较口径。来源冲突期间留空；字段单位、期间、身份或范围不符合预期则拒绝整份导出。

总折旧摊销、租赁费用、现金及证券合计、交叉持股、总有息债务以及剔除特殊项目税前利润仍无已审核目标映射，保留NULL。EBIT/EBITDA现从来源报告标准指标导出，[组成边界](tdx-supplementary-fields-20260925.md)明确保留，不因填列而批准原生估值。行情、信用评级、税率假设、地理分部、期权、租赁承诺、行业与WACC政策也不编造。补充证据不自动合并进标准事实。

`export_cells`逐单元格记录`available`、`missing_standard_fact`、`source_record_conflict`或`requires_separate_valuation_definition`，保留标准值、系数、事实ID、可用时间和归档哈希。标准事实缺失不进一步推断为源零、未披露或未审核；更细状态需查`financial-statements`。`metadata`记录源库/程序/导出器哈希、报告期、信息截止及取得时间；公司`data_as_of`为信息截止日期，不冒充实际下载日。

## 网页消费与估值

如需手动让网页读取该快照，在启动网页后端的环境中显式设置：

```bash
export US_CN_HK_DB_PATH=/root/alphalake/workspace/sqlite-export-new/valuation.sqlite
```

仅改变该进程选库，不合并到seed。原页面搜索与选择公司后，原客户端提交`{ticker, risk_free_rate}`；不上传政策文件、不新增参数表单。需要修改参数时沿用原页面。当前三家TDX快照必需字段与目标口径未闭合，因此返回422并显示原因，不产生价格。

构建前端后启动后端：

```bash
PYTHONPATH=valuation/backend python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

只读诊断（结构化输出仅供后端/API，不要求前端理解）：

```bash
curl http://127.0.0.1:8000/api/database/compatibility/SZSE:300866
```

返回公司/期间、年度季度34项列覆盖、28个公司快照字段存在性、必需缺项及已知语义阻断。覆盖不等于语义已审核。原普通`POST /api/valuation`和PATCH保留，不再绑定专用只读会话；当前未完成TDX到成功原生输入的认证。

此网页进程只读SQLite，不打开大DuckDB。大库任务继续使用独立systemd服务和1GiB限额。接口恢复不代表认可原默认无风险利率、行业回退等参数适用于A股；参数自动化尚未实施，具体范围见[规格](valuation-native-integration-spec-20260924.md)。

## 历史v2定制网页验收（2026-09-22，现已撤除该估值旁路）

固定2026H1报告期、2026-09-22零点信息截止，主库只读重导出三家公司：标准事实2,656条、实际出现183个标准字段，每家三表状态282项（共846项）。183是该样本实际有值字段的并集，不是目录覆盖率或全市场完成率；标准目录及三表供给计数仍见ADR020。原17列宽表仍为219项有值、321项缺标准事实、378项需要估值定义；网页已从完整标准载荷消费，不把宽表空列自动填满。

安克与苏泊尔在显式固定10%WACC、收入／资本3倍等基线参数下分别得到92.1504与20.9713元／股，Go原导出显式清空附注列表后，其余载荷与SQLite逐项一致；以这份同一TDX载荷及同一政策调用统一入口，DCF、最终值、WACC输出及run ID与网页一致。原先包含附注或不同政策的CLI运行具有不同run ID，不冒称是同一请求。这些数值只证明桥接一致，不替代之前采用不同预测/WACC/资产政策的估值，也不是推荐价格。茅台保留金融兼营拒绝，无普通模型每股值。安克3项、苏泊尔4项PDF补充输入全部排除；TDX快照消费前后SHA256不变。

结果：`workspace/sqlite-web-20260922/valuation.sqlite`；[收据含实际政策、源库哈希及运行ID](acceptance/sqlite-web-20260922.json)。该轮浏览器实测搜索、选公司、政策文件必填、条件结果和三表展示、错公司政策拒绝及茅台金融兼营拒绝，零浏览器运行异常。政策文件入口及后来的定制参数表单均已撤除，当前恢复原网页输入；该收据只保留当时验收事实。[浏览器复验说明](../valuation/frontend/README.md)。没有执行全市场导出或重做逐公司PDF核验。

首次构建还在原提交`f9c667b`独立复现已有TypeScript错误。本轮删除未用变量、修正nullable ref、缺值产生boolean的表达式、租赁开关来源及创建请求类型；未改估值公式，不通过关闭类型检查掩盖错误。前端构建仍提示既有主包超过500kB，未为了本次数据桥接做额外拆包。

验证收口：Python全套471通过、4项既有外部工作簿样本缺失跳过；最后单位元数据与名称搜索小修后，针对性4项回归通过。Go全套测试、当前CLI构建、前端构建、CI YAML解析及文档链接检查通过。最终快照重建后所有非元数据表逐项一致，重开API和Chromium复验通过；中间快照删除路径、哈希和原因已写入收据，临时测试库及编译程序已清理。源码和依赖锁文件中的估值公式/依赖未变。
