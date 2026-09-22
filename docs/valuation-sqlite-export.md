# 标准财务事实导出 SQLite

按用户要求提供显式兼容快照，长期采用API还是SQLite的数据层决策延后。工具不修改源DuckDB、网页默认seed或环境配置，不为旧版AlphaLake契约保留兼容路径。

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

链路：DuckDB标准事实→已有`export-valuation`只读契约→SQLite。复用当前`companies`、`financials_annual`、`financials_quarterly`结构和写入函数。金额为**百万元人民币**、股数为**百万股**；保留源精度，不补小数。日期锚点来自指定报告期，年表以最近完整自然年为FY0，季表以指定季末为FQ0。固定时点取数不是逐季度当时留存的数据版本认证。

| SQLite列 | 标准字段 | 转换 |
|---|---|---|
| revenues | revenue_cumulative | 累计流量 |
| net_income | net_income_parent_ytd | 归母净利润累计流量 |
| interest_expense | interest_expense | 累计流量 |
| capex | capital_expenditure_cash | 购建长期资产现金累计流量，非净再投资 |
| earnings_before_tax | profit_before_tax | 累计流量 |
| total_tax_expense | income_tax_expense | 累计流量 |
| r_and_d_expense | research_and_development_expense | 累计流量 |
| bv_equity | equity_parent | 期末归母权益 |
| shares_outstanding | total_shares | 期末股本 |
| minority_interests | noncontrolling_interests | 期末少数股东权益 |

年度流量取全年累计；单季流量为当年累计减前一季度累计，Q1直接使用。不跨年度差分、不加总期末余额；前期缺失则差分结果为空。追溯调整可能影响跨期可比性，本工具未独立审核比较口径。来源冲突期间留空；字段单位、期间、身份或范围不符合预期则拒绝整份导出。

EBIT、EBITDA、总折旧摊销、租赁费用、现金及证券合计、交叉持股、总有息债务没有直接等同的通用定义，本轮保留NULL。行情、信用评级、税率假设、地理分部、期权、租赁承诺、行业与WACC政策也不编造。补充证据不自动合并进标准事实。

`export_cells`逐单元格记录`available`、`missing_standard_fact`、`source_record_conflict`或`requires_separate_valuation_definition`，保留标准值、系数、事实ID、可用时间和归档哈希。标准事实缺失不进一步推断为源零、未披露或未审核；更细状态需查`financial-statements`。`metadata`记录源库/程序/导出器哈希、报告期、信息截止及取得时间；公司`data_as_of`为信息截止日期，不冒充实际下载日。

## 消费与边界

如需手动让网页读取该快照，在启动网页后端的环境中显式设置：

```bash
export US_CN_HK_DB_PATH=/root/alphalake/workspace/sqlite-export-new/valuation.sqlite
```

仅改变该进程选库，不是合并到seed。数据库读取格式兼容**不等于完整DCF输入或网页估值准入**；网页既有行业/国家默认参数和季度年份等问题没有在导出器中修复，见[审计](valuation-data-path-audit-20260919.md)。不要将缺项下网页自行补值的结果作为AlphaLake已验收估值。

回归（从仓库根目录，在受控服务中执行）：`PYTHONPATH=valuation/backend python3 -m unittest tests.test_alphalake_sqlite_export`。现有CI的Python pytest发现该测试，无新增解释器依赖包。

本轮真实验收：安克、茅台、苏泊尔，2026H1报告期／2026-09-22信息截止，30行年度和24行季度；918个单元格中219个有值、321个缺标准事实、378个需要独立估值定义。有值项从保留的十进制证据逐一复算，空值项逐一检查；源库哈希不变，独立服务峰值896.5MiB、swap为0。不是全市场验收，也未重新核对PDF。结果保存在`workspace/sqlite-export-20260922/valuation.sqlite`，见[验收收据](acceptance/sqlite-export-20260922.json)。
