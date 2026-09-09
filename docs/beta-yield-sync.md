# 全球行业 Beta 与人民币国债收益率同步

在 schema 27 上新增两个生产命令，复用国家风险链的运行记录、原始归档、内容签名、事务发布、失败诊断及离线重放。没有新增数据库迁移或依赖，不回写公司财务事实，也不自动修改估值政策或计算 WACC。

## 使用

在仓库根目录，使用已有 valuation Python 环境（Beta 需要 xlrd；中债解析只需 Python 标准库）：

```bash
go build ./cmd/alphalake
./alphalake sync-industry-beta workspace/wacc/alphalake.duckdb --python /absolute/path/to/venv/bin/python
./alphalake sync-cny-yield workspace/wacc/alphalake.duckdb --python /absolute/path/to/venv/bin/python

./alphalake sync-industry-beta workspace/wacc/alphalake.duckdb --python /absolute/path/to/venv/bin/python --offline
./alphalake sync-cny-yield workspace/wacc/alphalake.duckdb --python /absolute/path/to/venv/bin/python --offline
```

命令在目标库应用已有迁移，归档位于同目录 `raw/`。默认 `python3`；从其他工作目录执行时，使用 `--parser` 提供解析器绝对路径：

- Beta：`valuation/backend/data_sources/damodaran_parsers/beta_parser.py`，同目录必须保留 `xls_utils.py`。
- 中债：`internal/source/chinabond/parse.py`。

Beta 复用原有解析函数，在标准化边界额外锁定工作表、表头、区域、行业目录、样本数与源公式。实际执行的解析器及本地 helper 冻结到临时目录后运行，两者内容均计入版本签名；Python 与 xlrd 版本也计入签名。中债采用标准库 HTMLParser，忽略注释中的隐藏期限点，按表格结构读取可见内容。

## 全球行业 Beta

来源：[Aswath Damodaran / NYU Stern 全球 Beta 工作簿](https://pages.stern.nyu.edu/~adamodar/pc/datasets/betaGlobal.xls)。感谢原作者提供数据，保留 valuation 上游解析器 credit。2026 年 1 月 5 日工作簿的完整 94 行行业目录已审核并固定，不包含最后两行总市场汇总。

| 内容 | 保存规则 |
| --- | --- |
| 去杠杆 Beta | `beta_unlevered`，无量纲 |
| 现金调整后的去杠杆 Beta | `beta_unlevered_cash_adjusted`，无量纲 |
| 行业 D/E | `debt_equity_ratio`，小数比例 |
| 行业有效税率 | `effective_tax_rate`，小数比例，不等于去杠杆使用的边际税率 |
| Number of firms | 每行 `sample_count`；为来源行业公司数，不声称每项统计的有效样本数相同 |

合计 94 × 4 = **376 项**，写入 `reference.industry_stat`，`sample_region=global`、`statistic_code=provider_estimate`。分类目录保存为 `damodaran_industry_2026`，原始行业名称作为该版本来源节点标识；不覆盖申万等既有分类。

工作簿选用 Marginal 去杠杆税率，当前值 0.2537；Beta 的 method_code 记录此选择及完整标准税率。D/E 和有效税率的 method_code 为 `provider_reported`。现金调整与未调整 Beta 以不同 metric 区分。逐行业检查以下源公式闭合，但不将其称为独立市场估计：

```text
去杠杆 Beta = 来源杠杆 Beta / (1 + (1 - 来源边际税率) × 来源 D/E)
现金调整 Beta = 去杠杆 Beta / (1 - 来源 Cash/Firm value)
```

酒类现金调整 Beta 为 `0.725044343989`（217 家），消费／办公电子为 `1.192587261247`（122 家）。它们只是行业输入，**没有自动为茅台或安克分配行业**，分类 membership 表不增加公司归属记录。

2026 目录以外的新增、缺失、重名行业或未来年份会拒绝发布，需先核对新目录再升级契约；不能把固定目录当成永久通用分类。当前仅全球样本，尚未接入中国样本文件。

## 人民币国债曲线

来源：[中债公开国债及其他债券收益率页面](https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/more?locale=cn_ZH)，编制方为中央国债登记结算有限责任公司。页面说明为在岸人民币债券的到期收益率。数据使用条件以来源规定为准；本轮验证公开页面获取，不声称获得商业数据再分发授权或稳定 API 承诺。

只提取名称精确匹配的“中债国债收益率曲线”，不混入商业银行普通债或中短期票据。八个期限为 3、6、12、36、60、84、120、360 个月。表头日期必须与日期输入框一致，单位必须为 `%`；八列须完整。

写入 `market.yield_curve_point`：

- `curve_code=chinabond_government_pbc`、`currency=CNY`、`rate_type=yield_to_maturity`。
- `raw_unit=percent`，标准 value = 原值 / 100，保留 12 位小数。
- `compounding=unknown`、`day_count=unknown`；页面不足以确认，禁止据此进行需要这些口径的利率转换。
- 负利率允许，缺失、非数字或期限错位拒绝整个版本。

2026-09-09 归档中十年期原值 `1.6816`，标准值 `0.016816000000`。它是国债到期收益率，**不是已经扣除主权违约利差的无风险利率**，也不是债券即期曲线。

## 时间、重放与失败保护

两种来源的 observation_date 均取原文件／页面，不从文件名或抓取日期推断。available_at 保守采用首次取得该原始内容的时间，不从年初标签或页面所述常规日终时间倒推实际公开时间。

新发布与原始关联、完整观测、内容级检查点在同一事务提交；Beta 需要创建的分类目录和节点也在该事务内。失败不会留下部分行业目录。相同解释重放逐行比对值、单位、方法、来源定位及样本数；发现已发布记录被修改则报错，不静默覆盖。

离线模式选本地最近成功发布的本数据集归档，重新核验哈希；失败的新下载不会挤掉它。这里“最近成功发布”只定义运维重放，不是财务估值 ASOF 策略，不用到达顺序判定经济上的最新版本。所有旧版本保留，未实现自动更正关系与历史撤回。

## 验证

完整源文件与固定解析预期纳入测试目录；Python 的运行版本允许在预期比对时变化，但真实发布签名必须随环境改变。CI 已显式启用跨语言 Go 集成测试，并运行 Python 测试。

- Go：真实本地 HTTP 供给→归档→解析→发布→在线重复获取→坏文件失败→离线零 HTTP 重放→重开库检查失败行、诊断与原观测。
- 事务负向：人为制造观测唯一键冲突，验证发布版本、归档关联、分类目录和检查点一起回滚。
- Python：94 行 Beta 的冻结预期和独立 Decimal 公式复算；源税率选择、区域、行业重复、样本数和 Beta 篡改均有拒绝测试。公式核验仍使用 xlrd 解码，不声称有第二个独立 XLS 解析引擎。
- 中债：日期不一致、单位变化、期限错位、曲线名称变化、非数字值拒绝；八项百分数转换逐项精确核验。
- 国家风险：共享逻辑改造后原有完整验收继续执行，旧解释版本签名保持兼容。

后续已实现[参考输入显式时点／方法选择及目标资本结构桥接](wacc-valuation-bridge.md)。此次接入没有闭合这些政策判断，不能直接宣称两家公司 WACC 已由市场数据自动算出。

## 2026-09-09 实际执行记录

隔离库 `workspace/wacc-beta-yield-20260909/acceptance.duckdb`：

| 操作 | run_id | artifact_id | release_id | inserted | observations |
| --- | --- | --- | --- | --- | --- |
| 实际联网 Beta 同步 | 1 | 1 | 1 | true | 376 |
| 实际联网国债曲线同步 | 2 | 2 | 2 | true | 8 |
| Beta 离线重放 | 3 | 1 | 1 | false | 376 |
| 国债曲线离线重放 | 4 | 2 | 2 | false | 8 |

此外，上一轮国家风险验收库执行离线重放得到 `run_id=3, release_id=1, inserted=false, observations=10`，验证公共逻辑整理未改变旧解释签名。

全套 Go 测试在设置 `ALPHALAKE_TEST_PYTHON` 的环境执行通过，跨语言真实归档测试未跳过；构建、vet 通过。Python 后端 157 passed / 4 skipped，新增 12 项全部执行，4 项跳过为既有外部条件测试。CI 已加入两条新链的强制离线验收。零依赖变化、零新增迁移；没有修改既有公司财务生产库或 WACC 默认参数。workspace 验收库不提交，完整原始测试归档及预期已入库。
