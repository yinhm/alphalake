# 全 A 股条件估值自动化验收（2026-09-10）

已实现从本地标准财务、行业与风险参考，到政策路由、批量估值、结果留档及失败分项的自动执行。本次本地证券分母5,569家全部有处理结论，2,461家产出条件股权估值。核心财务齐全率95.24%，条件估值产出率44.19%；二者分母相同、含义不同。

这不是5,569家均已取得可靠公允价值，也不是交易所完整名单验收。报告期为2026-06-30，披露截止2026-09-10T04:42:23Z；身份补证使用当前本地可核验知识，不能冒充当时已有的历史数据库快照。未来收入、利润率、再投资、信用档及权重含明确政策；历史FCFF缺口保留，不能拿输出当当前目标价。

## 实际结果

| 估值处理状态 | 公司数 |
| --- | ---: |
| 条件股权估值 | 2,461 |
| 输入或政策不适用 | 2,340 |
| 缺模型输入 | 166 |
| 无已审核行业政策 | 598 |
| 公司专项审核隔离 | 3 |
| 源记录冲突 | 1 |
| 执行异常 | 0 |
| 合计 | 5,569 |

财务就绪度单独为5,304核心输入齐全、235缺字段、29无标准事实、1源冲突。估值状态先检查政策准入，因而“缺模型输入166”不等于“所有财务不完整公司”；无政策组中也存在财务缺项。

2,340项拒绝分别为：1,499收入/调整EBIT非正，531股权残值非正，258股本或账面索偿/现金符号不适用，50存在金融业务字段，1公司/WACC范围不匹配，1生成的目标利润率超出合法范围。它们需要数据核验或不同模型，取消检查不是修复。

## 验收依据

78行业批次实际完成2,404份成功结果；48条北交所代理规则的独立批次完成57份。本次汇总取前者的5,223家沪深证券、后者的346家北交所证券，保留逐行原批次及run ID。**这是两批分区验收汇总，不伪称一次跨库原子运行。**

汇总校验逐项确认：证券ID集合及分母相同、报告期/截止相同、引擎摘要相同、WACC及资本参考包完全相同；组合政策恰为原78规则加48规则，逐证券路由及分类证据与被采用批次相同。全部2,461份成功请求已另以Decimal重建标准输入TTM、同比规则、行业WACC、十年现金流、终值和股权桥接，成功run ID集合完全一致。

财务主库已在07:51:58Z更新为已验收的schema37副本，标准事实2,023,155条；原库备份保留。发布前精确集合比较证券身份、分类、映射、补充输入，以及沪深估值导出的财务内容、原始位、归档SHA、公告链接和倍率，差异全部为0。仅排除物化刷新造成的运行编号/写入时间变化；这两列不参与当前ASOF排序。重开发布后的主库重新扫描，与验收副本JSON完全相同。

摘要及产物SHA固定在[验收清单](acceptance/a-share-20260910.json)。完整逐证券结果、汇总断言在本地`workspace/a-share-acceptance-20260910/acceptance.json`与`verify.py`；它们依赖本地完整数据库/归档请求，不属于裸克隆CI样本。重验命令（仓库根目录、使用已安装后端依赖的Python）：

```bash
PYTHONPATH=valuation/backend python workspace/a-share-acceptance-20260910/verify.py
```

跨公司原文校验的范围单列：既有安克/茅台生产链样本保留；本轮[安徽凤凰](../internal/ingest/testdata/bse-valuation-2026/README.md)新增32项标准输入核对，27项数字匹配源float32位、5项PDF空白/横杠不证明数值零。六期TDX切片、四PDF及台账进入Go/Python离线CI。独立DCF复算不是2,461家公司逐份PDF语义审核。

## 实际运行入口

在仓库根目录构建`go build ./cmd/alphalake`，准备已有后端依赖的Python环境，然后：

```bash
cd valuation/backend
mkdir -p data
python -m tools.prepare_industry_policy \
  ../examples/a-share-nonfinancial-recipe-2026H1.json \
  --bse-crosswalk ../examples/bse-shenwan-proxies-2026H1.csv \
  > data/combined-policy.json

ALPHALAKE_DUCKDB_MEMORY_LIMIT=1024MiB ALPHALAKE_DUCKDB_THREADS=1 GOMEMLIMIT=128MiB \
python -m tools.batch_valuate_alphalake \
  /root/alphalake/workspace/auto-valuation-20260909/market.duckdb \
  --period 2026-06-30 --as-of 2026-09-10T04:42:23Z \
  --policy data/combined-policy.json \
  --reference-database /root/alphalake/workspace/industry-capital-20260910/references.duckdb \
  --output-dir data/combined_batches
```

此命令会真实重跑完整组合批次，耗时较长；本次已验收的是上文分区实测汇总。无需启动HTTP服务。成功请求及结果按内容留存于`data/alphalake_runs`，批次报告保留全分母与失败原因。

日常同步到估值使用相同组合政策：

```bash
ALPHALAKE_DUCKDB_MEMORY_LIMIT=1536MiB ALPHALAKE_DUCKDB_THREADS=1 GOMEMLIMIT=192MiB \
python -m tools.refresh_valuate_alphalake \
  /root/alphalake/workspace/auto-valuation-20260909/market.duckdb \
  --period 2026-06-30 --filings-start 2025-04-01 --latest 6 \
  --policy data/combined-policy.json --stage-timeout 21600 \
  --reference-database /root/alphalake/workspace/industry-capital-20260910/references.duckdb \
  --sync-references --output-dir data/refresh_runs
```

刷新命令默认在同步结束后选择信息时点，不固定到本次验收日期；依次采集、补公告、物化、同步参考、批量计算。运行日志/退出码与批次覆盖分别检查，部分失败不能当全量刷新成功。内存和六小时阶段上限为本机部署参数，不是资源或完成时长保证。已有入口可交cron/systemd调用，未安装后台周期任务。

新报告期仍需审核并更新政策；不能自动把2026H1政策延用到2026Q3。公司行业名单的首次取得时间不会因重复下载相同文件而刷新，超过政策七天采用窗口会明确阻断；这不是上游最近更新时间，需要后续另立来源观察新鲜度契约，不能修改原始first_seen来掩盖。

## 后续真正限制自动化的事项

1. **可核验的来源覆盖**：22家公司公告机构身份仍缺失；两处同日同名完整报告需原文版本关系核验；235缺字段/29无事实逐类补齐。先解决来源，不补默认零。
2. **模型适用范围**：普通FCFF无法自动处理负经营收益、困境股权、金融/地产或混合业务。应先按实际拒绝分类选模型和真实样本，不能用乐观利润恢复假设批量消除阻断。
3. **行业及参考更新契约**：北交所80个类别未映射，598家公司无准入政策；继续用来源依据审核。完善重复取得/首次取得/来源发布日期的区分，再做跨期政策更新。
4. **当前证券价值**：全市场公司/类别股本、A/H股、稀释、期后融资和现金、债务市场价值及地域暴露尚未全自动。现有行业目标权重WACC是代理，不能改称逐公司的市场WACC。
5. **运行效率与持续回归**：大批次目前逐公司查询并重新执行引擎；先依据实测优化重复导出，再增加周期部署。继续扩大真实财报语义抽核，独立算式核验不能替代它。

本轮交付是有明确拒绝边界、可重复执行的全本地A股条件估值链；剩余事项保留为后续可验收工作，不计入已经完成的估值覆盖。
