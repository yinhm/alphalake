# 数据更新、验收与网页发布

本页是日常操作契约；当前分母、版本及缺口统一见[项目状态](../implementation-status.md)，旧性能、故障及发布记录见[历史验收](../history/system-delivery-20260927.md)。网页保留原流程，SQLite为可重建快照，不能把显式政策CLI成功当成网页已发布。

## 原生网页交付入口

`tools.publish_native_valuation`复用现有同步、导出、API和共享引擎。它不改变原前端，不使用显式政策CLI替代原生API验收。模型默认与参考消费边界见[原生输入政策审计](../history/native-input-policy-audit.md)。

输入必须明确：主库、发布目标、报告期、证券清单、来源模式、验收无风险利率，以及读取该发布目标的本地网页地址。清单每行一个六位代码；不能以成功公司反推清单。无风险利率是验收假设，不是自动采集的公司WACC。

本机示例（先确认8080读取目标SQLite、没有其他大任务在运行）：

```bash
ALPHALAKE_REPO="$PWD"  # 在项目根目录执行
systemd-run --unit=alphalake-native-publish \
  --property=WorkingDirectory="$ALPHALAKE_REPO" \
  --property=MemoryMax=1G --property=MemoryHigh=896M \
  --property=MemorySwapMax=0 --property=OOMPolicy=stop \
  --setenv=PYTHONPATH="$ALPHALAKE_REPO/valuation/backend" \
  --setenv=ALPHALAKE_WORKSPACE="$ALPHALAKE_REPO/workspace" \
  --setenv=ALPHALAKE_DUCKDB_MEMORY_LIMIT=1GiB \
  --setenv=ALPHALAKE_DUCKDB_THREADS=1 \
  --setenv=GOMEMLIMIT=512MiB --setenv=GOMAXPROCS=1 \
  --setenv=GOPATH="$(go env GOPATH)" --setenv=GOCACHE="$(go env GOCACHE)" \
  "$ALPHALAKE_REPO/.venv/bin/python" -m tools.publish_native_valuation \
  --database workspace/alphalake.duckdb \
  --output workspace/derived/valuation.sqlite \
  --period 2026-06-30 \
  --codes-file workspace/derived/system-delivery/published-codes.txt \
  --source-mode offline --latest 6 \
  --risk-free-rate 0.04 --web-url http://127.0.0.1:8080
systemctl show alphalake-native-publish -p ActiveState -p ExecMainStatus -p MemoryMax
journalctl -u alphalake-native-publish --no-pager
```

清单由操作者事先固定；当前网页为固定100家加2家回归证券。扩大或缩小范围须单独验收，不能按成功结果筛清单。源码新增不等于该命令全部模式均已在线验收，当前验收边界见文末。

刷新同一范围时沿用相同清单、报告期与验收利率；`online`另外传入公告目录日期范围。成功后取`.delivery.json`所指`run.json`中的`information_as_of`，用相同`--as-of`、`--source-mode local`重放，可验证同输入不重导、不重算且网页文件不替换。仅改变显式验收利率时，SQLite可复用，但必须重新验算及真实HTTP核对，不将旧利率的结果当作新结果。

| 模式 | 实际动作 | 来源新鲜度 |
|---|---|---|
| `local` | 仅导出既有标准事实、验收和发布 | 不检查上游，不声称完成同步 |
| `offline` | 本地财务缓存源同步、物化，再导出验收发布 | ZIP核验合格不等于已确认上游最新 |
| `online` | 财务同步、限定清单的公告目录与行情同步、物化、验收发布 | 须查看来源日志及缓存回退，不能用进程成功代替最新性 |

在线模式另需`--filings-start`和可选`--filings-end`，默认结束日为中国当日；行情使用`sync-valuation-quotes`，只追加估值信息截止时最近已完成收盘日前14个自然日至该日的观测，价格时点与财报和股本基期独立，不更新完整日线及检查点；身份来自已有审核快照并逐日期核对，不按证券代码前缀猜市场。见[窗口同步](valuation-quote-window.md)。首次建立新范围先用local模式验证导出与身份；公告目录用于补充证据，不是当前TDX数值的准入前置；不会自动审核源零或解决真实数值冲突。

可选`--sync-references`同步既有六类参考及23份原生US/Global行业与国家税率工作簿，offline模式重放已注册归档。原生参考随SQLite v10发布，会话绑定参考内容版本；实际网页与候选结果不同会拒绝发布。全局没有安装定时器，也不新增常驻任务平台。

## 只读参考关联审核

`tools.audit_reference_coverage`复用官方公司名单解析器及原生参考加载器，核对工作簿、SQLite参考和完整证券覆盖盘点的关联血缘；输出行业/国家缺项、分类冲突及TDX同业候选。候选不自动写库或替换默认政策，不运行DCF，也不重新判定财务完成度。

在按[资源隔离](fundamental-memory-20260919.md)配置的独立服务中执行：

```bash
PYTHONPATH=valuation/backend .venv/bin/python -m tools.audit_reference_coverage \
  workspace/derived/valuation.sqlite \
  workspace/derived/financial-availability/coverage.json \
  workspace/damodaran/indname-a1f4d70aa2cf.xls
```

输出保存在`workspace/derived/reference-association/`。旧盘点可复用证券分母与参考关系，但必须逐条通过现行来源版本、SHA-256、代码及定位校验；不能复用其旧财务准入数量。官方表缺精确证券条目时，重同步不能生成发行人关联；另一股类条目须另有同发行人证据，行业或国家代理须作为显式政策审核。原生国家参考范围不足与公司国家条目缺失分别处理。

### 同发行人参考的默认关联

`tools.verify_issuer_reference`复验人工审核的A/H同发行人关联。交付单位是通用规则：官方三张名单一致、原行定位及哈希有效；CNINFO目录公告对应目标证券；正文同一公司信息/股票简况明确列出中英文法人全称、股类代码及交易所。英文全称只忽略大小写和空白，不做模糊匹配。集团、母子公司关系不接受；已有精确沪深源条目不能覆盖。原名单仍为5,100条，不把H股原行改写成官方A股记录，不新增H股行情同步。

```bash
PYTHONPATH=valuation/backend .venv/bin/python -m tools.verify_issuer_reference \
  --manifest workspace/derived/issuer-reference/reviews.json \
  --workbook workspace/damodaran/indname-a1f4d70aa2cf.xls \
  --coverage workspace/derived/financial-availability/coverage.json \
  --workspace workspace > workspace/derived/issuer-reference/inputs.json
```

在独立限额服务中执行。审核清单声明`same_legal_issuer_different_share_class`，包含审核者、时间、说明及五类页码/原文锚点；程序检查证据是否仍成立，**不代替人工判断法人关系**。`publish`记录更新或撤销均以`supersedes`绑定上一事件内容哈希，`revoke`移出活动输出，恢复须接上撤销事件；不删除旧审核。输出锁定工作簿、完整证券盘点及审核清单内容，逐项保留原证券代码、原行和正文出处。

审核入库命令会重新执行上述原文/来源校验，不接受已生成的覆盖请求代替证据。在独立限额服务内，先对schema56主库副本显式升级，再导入：

```bash
./alphalake upgrade-issuer-references workspace/alphalake.issuer-candidate.duckdb
./alphalake import-issuer-references workspace/alphalake.issuer-candidate.duckdb \
  --manifest workspace/derived/issuer-reference/reviews.json \
  --workbook workspace/damodaran/indname-a1f4d70aa2cf.xls \
  --coverage workspace/derived/financial-availability/coverage.json \
  --workspace workspace --python .venv/bin/python
```

schema57的`reference.issuer_industry_review`保存不可变验证包、完整事件链及证据归档关联；不是另建公司财务事实或多股类行情表。重复导入幂等，不能删去旧事件或重放旧短清单。最新审核包撤销或工作簿版本不匹配时不回退旧关联；更新官方工作簿后须按新原行重审。撤销与恢复均先导入候选、重导出及验收，再发布，不追改既有冻结会话。

默认路径为主库审核关联→SQLite `reference_issuer_association`→原生参考加载器→原会话。精确沪深来源条目优先，用户显式覆盖仍优先；来源代码、审核哈希、原文及官方工作簿版本分别保留，不把关联说成官方A股原行。原名单仍为5,100条。原工具的`request`仅为可选显式调用输出，网页使用默认关联无需上传JSON。

当前SQLite财务契约为v10（[期间转换spec](../valuation/data-contract.md)），原生参考契约为v2；旧参考v1须重建，不能改标签或运行时回退。先核对财务和身份/行情内容未变、原样本结果及新增关联，再协调主库、SQLite、匹配后端重启及真实HTTP复验；失败恢复旧数据和对应运行代码。契约升级需要重启，普通同契约数据发布按既有流程处理。

人工核验关联数量、默认参考齐备和估值成功分别统计；缺行情/财务继续保留。来源行业不是公司经济适用性认证，不为符合直觉改写官方标签；TDX同业推断等政策代理仍待审批。

## 失败与重放

- 所有阶段串行；同步下载/主数据失败、物化/导出/验收失败均返回非零，保留日志，不继续发布。财务源仅因待解析记录返回非零时，必须通过结构化完整收据证明没有下载/主数据错误、选中包的待解析项在报告期能唯一解析标准证券身份、与导出目标身份不相交，才允许继续；来源仍标记partial，受影响包不推进检查点。主库各来源已提交的有效事务保留，不把整轮失败误解释为主库从未更新。
- 候选SQLite在同目录创建；验收核对完整分母、准入及实际计算。ready却计算失败拒绝发布；财务缺项与`blocked_reference_inputs`分别统计，两类422均保留。
- 切换前记录发布日志并硬链接旧文件，原子替换后逐公司核验真实网页诊断、实际输入及最终结果。不重启网页；旧会话仍是原输入版本，新请求读取新快照。
- 网页复验失败立即恢复旧文件；切换过程中被强杀，下次同命令先恢复未提交切换，再重新执行。日志不证明后台仍在运行，应查systemd单元。
- SQLite内容（含参考快照）、程序/依赖、冻结模型参考及请求均相同时，复用已验收结果；源库物理内容和二进制也相同时不再导出。来源日志等元数据改变可能需要重新比对，不强行跳过质量检查。
- 只有导出时间或主库物理哈希变化、有效内容未变时，不替换网页文件。信息截止不同仍属于新的输入版本，不能省略披露。
- 成功收据位于目标旁的`.delivery.json`，切换日志为`.publication.json`，逐轮日志与比较在`workspace/derived/system-delivery/run-*`。后端和冻结模型参考哈希记录在每轮`runtime-inputs.json`，源参考发布/哈希随SQLite携带，不将这些动态文件提交仓库。


## 验收边界

指定102家范围的在线周期已完成真实验收：财务清单请求三台节点均为空，明确使用合格缓存；公告目录、9月30日收盘窗口、物化、候选API、原子发布与8080逐家复验完成。100成功、2项已知拒绝均保留；不称财务上游最新，未刷新参考上游。相同截止的local重放不重导、不重算、不替换网页文件；来源阶段故障保持可信网页文件，隔离副本的真实HTTP故障触发切换回滚。

当前DuckDB预算1GiB、Go软预算512MiB、服务硬上限1GiB的缓存刷新及重放也已验收；资源预算涵盖整个服务组。详细测量及限制见[本轮验收](../history/scoped-refresh-publication-20260930.md)。未安装自动定时调度；每次发布保留目标证券分母、财报期、财务/参考截止、来源新鲜度、拒绝与失败，不从成功公司反推清单。个别审核数据发布仍须候选比较、重开及真实API检查。

### 契约升级的一次性协调切换

当前v10需从主库重导出并复用已有prepared TTM入口；先完成候选API、原表/数值比较，再协调原子替换SQLite与后端重启，等待健康检查后逐家真实HTTP复验，失败恢复旧快照与冻结旧提交运行时。不能让新后端静默消费v9或修改标签冒充重建。主库schema57及参考v2不变，成功后清理回滚运行时和副本并留哈希；本轮收据见`workspace/derived/cumulative-ttm/publication.json`。

以下为schema56 / SQLite v9历史切换，不能当作当前命令要求：

先在未发布主库副本执行`upgrade-financial-availability`和物化，比较全部旧非空金额、日期来源及重放结果，再替换主库。SQLite v9须从新主库重导出，不能只改旧文件版本号。旧v8与新v9切换属于显式契约升级：保留旧快照备份，先完成候选原生API验收，再协调后端数据适配加载、原子替换及真实HTTP复验，失败恢复旧文件与对应实现；不得用旧运行成功记录替代验收。

在线流程仍补采CNINFO公告元数据，但该可选步骤失败或超时只记录退出码与日志，不阻断合格TDX当前数值的物化及导出；证券身份、数值来源、行情与必要参考失败仍按原规则处理。
