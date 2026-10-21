# 日常手工同步

本文是入口与核验指引；参数和语义以链接的当前指南、spec及实际程序为准。所有命令从已定位的项目根目录运行，重型命令必须在Skill所述独立限额服务内执行，不能直接在交互终端启动。

## 状态与参数

先读[项目状态](../../../docs/implementation-status.md)、[workspace布局](../../../docs/guides/workspace-layout.md)；必要时用`./alphalake status workspace/alphalake.duckdb`查询主库。网页财务范围及运行版本来自`workspace/derived/system-delivery/valuation.sqlite.delivery.json`指向的`run.json`、`codes.txt`和`freshness.json`；来源未请求、旧缓存及报价滞后分别保留。

从现有配置设置变量，不把历史示例当默认：`ALPHALAKE_DB`为主库，`ALPHALAKE_WORKSPACE`为配套归档根，`ALPHALAKE_PYTHON`为已安装后端依赖的解释器，`ALPHALAKE_RECEIPTS`为本轮新收据目录。报告期、窗口、范围及网页地址按对应任务核对；创建收据父目录，保留前后查询和版本，不向workspace顶层散放哈希与日志。

## 业绩预告刷新

必读[采集入库指南](../../../docs/guides/earnings-guidance.md)和[独立契约](../../../docs/earnings-guidance-spec.md)。普通刷新复用已确认的报告期、范围文件和上次完整公告窗口起点；结束日延至本次中国日期，不缩短窗口或自行追加其他报告期。示例变量`GUIDANCE_PERIOD`、`NOTICE_START`、`NOTICE_END`、`GUIDANCE_SCOPE`均来自这些配置。

```bash
./alphalake earnings-guidance "$ALPHALAKE_DB" \
  --workspace "$ALPHALAKE_WORKSPACE" --period "$GUIDANCE_PERIOD" \
  --scope-file "$GUIDANCE_SCOPE" > "$ALPHALAKE_RECEIPTS/before-query.json"

./alphalake sync-earnings-guidance "$ALPHALAKE_DB" \
  --workspace "$ALPHALAKE_WORKSPACE" --period "$GUIDANCE_PERIOD" \
  --start "$NOTICE_START" --end "$NOTICE_END" \
  --scope-file "$GUIDANCE_SCOPE" > "$ALPHALAKE_RECEIPTS/sync.json"

./alphalake earnings-guidance "$ALPHALAKE_DB" \
  --workspace "$ALPHALAKE_WORKSPACE" --period "$GUIDANCE_PERIOD" \
  --scope-file "$GUIDANCE_SCOPE" > "$ALPHALAKE_RECEIPTS/after-query.json"
```

各命令顺序执行、分别检查退出及stderr。返回契约是`earnings-guidance-ingest-v1`和`earnings-guidance-context-v1`。范围来自既有分类，普通刷新复用已验证的`workspace/derived/earnings-guidance/scope.json`；文件缺失或范围已主动改变时先核对分类依据，不能从“有预告公司”反推分母。同步锁内再次冻结范围；不依赖旧文档中的公司数。

核对每来源／窗口run ID、`status`、`source_rows`、`observations`、`inserted`、`documents`及`document_failures`。东财按报告期返回取得时的当前预告，巨潮窗口按公告日期，不能称二者共享历史截止。默认同时归档PDF；只有用户明确只要目录时才用`--metadata-only`。分页数量变化由既有有限窗口拆分处理，拆分后仍失败就保留失败，不能去重拼接不完整页冒充完成。

用同一命令追加`--offline`并另存收据，再重开查询；检查未变化输入新增观测／关系为0、查询逐字节一致，归档损坏必须失败。离线仅证明归档可重建，不认证此时上游最新。

比较前后查询：新公司、新公告、同事件内容修订、仅公告身份关联分别报告。`linked`只说明唯一身份关联，不表示金额核验；有PDF但无东财区间仍只有公告证据。`no_observation_stored`不是预测为零。预告不得填正式报表／TTM、不自动取区间中点、不用净利润替代EBIT、不自动修订故事或网页默认；本任务不重导SQLite。

## TDX财务刷新

读取[本地财务同步](../../../docs/guides/tdx-offline-sync.md)。沿既有历史窗口及发布报告期设置`FINANCIAL_PERIOD`和`FINANCIAL_LATEST`，避免未来占位包或为凑齐历史扩采早年。

```bash
./alphalake sync-financial "$ALPHALAKE_DB" \
  --through "$FINANCIAL_PERIOD" --latest "$FINANCIAL_LATEST" \
  --report "$ALPHALAKE_RECEIPTS/financial-source.json"
./alphalake materialize-fundamentals "$ALPHALAKE_DB"
```

在线优先校验`tdx-cache/gpcw.txt`及本地ZIP；清单未变不重复下载。连接按上游`tdx.Hosts`顺序、单次最多三台，禁止另找HTTP后备、重启或分段重组。上游失败可以明确使用可信缓存，不称最新；显式离线加`--offline`，不触网。只有确实要处理全部缓存时才用`--all`。

源维护收据`alphalake-financial-sync-v1`列运行、缓存回退和错误；partial时先核对哪些范围实际提交，不凭非零退出把全部成功事务丢弃，也不把旧事实冒称本轮完成。标准物化后重开检查增改删及缺项。公告日期／CNINFO关联不足不阻断有效当前数值，单位、身份、期间及语义冲突仍拒绝。单纯财务刷新不代表网页更新。

## 行情、参考与公告

- **收盘行情**：读取[估值窗口](../../../docs/guides/valuation-quote-window.md)，从已有审核身份／现行发布范围取得symbols，批量用`./alphalake sync-valuation-quotes "$ALPHALAKE_DB" --symbols "$QUOTE_SYMBOLS" --date "$QUOTE_DATE" --report "$ALPHALAKE_RECEIPTS/quote-source.json"`。交易所日历核对最近已完成交易日，周末／节假日不造新报价；不抓全历史、不推进全日线检查点。partial、空响应及旧报价分别报告。
- **人民币利率**：仅该来源刷新用`./alphalake sync-cny-yield "$ALPHALAKE_DB" --python "$ALPHALAKE_PYTHON" --parser internal/source/chinabond/parse.py`；来源发布日、生效日和取得时间分开，不能把国债收益率叫公司WACC。
- **达摩达兰等既有参考**：读取[发布指南“只刷新参考和行情”](../../../docs/guides/system-delivery.md#只刷新参考和行情)，复用`tools.refresh_valuate_alphalake.reference_commands`的当前完整命令清单，串行执行并逐项留收据，不复制另一份数据集／下载地址列表、不另建参考库。US／Global不互换，缺公司关联不猜映射。刷新参考不自动批准新预测参数。
- **正式公告目录／原文**：仅请求该来源或有明确核验用途时，沿`sync-filings`及[数据接入spec](../../../docs/valuation/data-contract.md)确定公告窗口与证券范围；预告任务用上面的独立入口。不要为日常财务可用性全量扩采PDF或建立PDF数值主源。

这些任务只更新主库。若同时要求网页交付，沿下一节发布；参考／行情已完成在线刷新时，保存真实同步日志与local发布之间的关联，不把local收据改称在线全来源刷新。

## 宏观定期统计

读取[宏观来源与口径](../../../docs/guides/macro-data.md)。仅沿现有统计局、人民银行、财政部发布目录刷新；无需证券范围、财报报告期或估值重算。用户只要求宏观时不附带财务／报价同步，已有国债与汇率沿原入口，不另存宏观副本。

```bash
./alphalake sync-macro "$ALPHALAKE_DB" \
  --workspace "$ALPHALAKE_WORKSPACE" --source all > "$ALPHALAKE_RECEIPTS/macro-sync.json"
./alphalake sync-macro "$ALPHALAKE_DB" \
  --workspace "$ALPHALAKE_WORKSPACE" --source all --offline > "$ALPHALAKE_RECEIPTS/macro-replay.json"
./alphalake macro-series "$ALPHALAKE_DB" > "$ALPHALAKE_RECEIPTS/macro-series.json"
./alphalake macro-data "$ALPHALAKE_DB" --latest > "$ALPHALAKE_RECEIPTS/macro-latest.json"
```

`--source`可限定`nbs`／`pbc`／`mof`。逐个核对`alphalake-macro-sync-v1`收据的请求／成功数据集、统计期、发布ID、观测与新增、未来空占位和失败；离线新增应为0且版本不变。原文坏档必须失败，partial不能称全量最新。各类最新期不同，不用今天或URL日期补发布时间；百分比已规范为小数，PMI仍为指数点。金额、同比及可比口径不可混算。

宏观保留来源修订，不宣称严格历史PIT。它为经营判断提供证据，不自动把GDP／CPI／信贷变化代入公司增长、利润率、永续增长或WACC；不导出到财务SQLite，不发布网页财务库。当前库版本与迁移先查状态页，不能对旧库直接强制初始化。

用户同时要求宏观网页交付时，读[宏观阅读发布契约](../../../docs/guides/macro-pages.md)，使用`tools.macro_pages publish`，从同版主库取得快照、逐条核对点评中的系列／期间／值，再发布`workspace/macro`。点评稿放其`editorial/`，明确作者、审核时间及限制；新数据与旧点评证据不符必须拒绝，不能跳过校验强发。使用新期次ID修订，相同内容重放复用原期次；`validate`及实际`/api/macro/latest`、`/macro`／历史固定链接核验成功才称页面更新。请求侧不启动Go或读DuckDB，宏观发布不走财务SQLite发布器。

现有历史曲线来自本期来源提供的旧期间，历史页面只从真实发布起积累；不为凑历史归档回造旧点评。普通`sync-macro`只更新主库，不等于阅读页已发布；只有要求宏观页面交付时才执行上述发布，不自动安装调度。

深化投资研究时按[宏观到投资](../../../docs/guides/macro-investment.md)操作：用`tools.macro_pages compare --before <前期ID> --after <本期ID>`区分新统计期、补历史、覆盖扩大、值修订、仅来源及口径变化。来源ID或导出时间变化不等于经济变化；缺共识不称超预期。点评按消费、制造设备、成本价格、地产、出口、信用财政选择有关经营暴露，写支持／反证、公司取证及下一观察事件，不自动改参数或发受益股排名。长专题沿已有`research_note`登记并直接引用宏观固定文件；不重复拷贝数据，新报告与宏观页面相互链接。只有点评更新时如实报告标准观测变化为0。

## SQLite与网页交付

必读[统一发布指南](../../../docs/guides/system-delivery.md)。用户要求刷新并发布时，沿`tools.publish_native_valuation`执行，不能只复制导出的SQLite。发布目标、实际消费该目标的`WEB_URL`、财报期必须明确；沿`--published-scope`在锁内冻结现行完整范围，首次建范围或主动扩缩时使用经核验的`--codes-file`。

```bash
PYTHONPATH=valuation/backend "$ALPHALAKE_PYTHON" -m tools.publish_native_valuation \
  --database "$ALPHALAKE_DB" --output workspace/valuation.sqlite \
  --period "$FINANCIAL_PERIOD" --published-scope \
  --source-mode local --web-url "$WEB_URL"
```

`local`仅导出已有事实、原生API验收及原子发布，不能加`--sync-references`或报价日期。`offline`重放财务缓存，可按请求加`--sync-references`，不刷新行情；`online`必须有显式最近已完成交易日`--quote-date`，既有参考可加`--sync-references`。公告目录只按请求补采，不为此暗中扩大来源。

读取发布收据及实际HTTP复验，成功才报告网页已更新；失败回滚，保留候选、来源失败、拒绝和完整分母。同信息截止的local重放应复用已验证内容；仅元数据变化不要求替换网页文件。没有明确发布目标时完成可独立验证的来源刷新，列明仍缺的部署信息，不能猜主机或把主库更新称生产发布。

季度预告必须分别看标题截止期和实际经营起始日：v3对无法区分累计／单季的季度标签保留`unknown`，不将其自动计为7–9月。解释版本升级沿同一窗口离线重建，新增ID先判断是否仅重解释；来源取得时间、公司数和金额需另行比较，不能称为新增上游公告。具体以[预告期间契约](../../../docs/earnings-guidance-spec.md#季度标签的期间边界v3)为准。

机器检查主库用`./alphalake status workspace/alphalake.duckdb --json`，先看`schema_supported`；旧的人类文本输出不能直接`json.load`。累计validation计数不代表本轮新增失败。发布stdout为JSONL，当前结果读delivery收据指向的`run.json`；计算复用的`web_checks`没有`http_status`，应核对实际`calculation=reused_verified_inputs`、完整范围及发布哈希。不要把收据格式判断错误当来源或网页失败而重复在线同步；多命令包装须前置失败即停。详细契约见[状态与收据](../../../docs/guides/system-delivery.md#状态与收据的机器读取)。
