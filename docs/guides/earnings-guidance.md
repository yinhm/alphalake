# 业绩预告采集与入库

公司预告独立于正式财务，金额是公司估计，不填报表、TTM或网页默认。结构化来源为东财，巨潮提供公告目录及原文；TDX预告目标期仍待审核。完整语义及后续边界见[预告契约](../earnings-guidance-spec.md)。

## 范围与运行

范围文件使用原生ticker，明确已有分类依据，只纳入沪深非金融公司：

```json
{"source":"既有完整发布的分类验收收据","tickers":["SZSE:300054","SHSE:600519"]}
```

未知或重复ticker拒绝；源响应中的其他证券完整归档，目标范围之外的数值不写预告宽表、不进入查询分母。没有预告不增加估值准入要求。

主库当前schema58。旧schema57先复制到`workspace/derived/earnings-guidance/candidate.duckdb`，在独立1GiB服务内显式升级：

```bash
./alphalake upgrade-earnings-guidance workspace/derived/earnings-guidance/candidate.duckdb
```

升级会比较既有表的行数与聚合内容签名，只有相同时才提交；不重算财务。副本重开、真实入库及原财务核对通过后才替换主库；保留可验证回滚，切换后清理备份。正常当前schema使用下列入口，资源配置沿[内存隔离](fundamental-memory-20260919.md)。

```bash
./alphalake sync-earnings-guidance workspace/alphalake.duckdb \
  --workspace workspace --period 2026-09-30 \
  --start 2026-01-01 --end 2026-10-09 \
  --scope-file workspace/derived/earnings-guidance/scope.json

./alphalake earnings-guidance workspace/alphalake.duckdb \
  --workspace workspace --period 2026-09-30 \
  --scope-file workspace/derived/earnings-guidance/scope.json
```

日期窗口和报告期均显式填写；窗口限制巨潮公告日期，东财按报告期返回当前预告，不把窗口结束日称为东财历史信息截止；手工刷新，不安装调度。范围读取与整个同步受workspace锁保护。东财按报告期分页，巨潮复用90日窗口、每页30条、默认排序（显式按时间排序会使同日公告跨页不稳定）；不相信上游`totalpages`，按总数、实际行和去重核验完整性。若数量变化或跨页重叠，二分日期窗口，最小一天；完整叶窗口的划分与页面清单保存到收据，下一次在线复用划分，离线不重新探测。

返回JSON包含证券分母、每来源／窗口的run ID、完成／失败／partial、指标行数、目标观测数、排除范围、诊断与PDF情况。HTTP200业务错误拒绝该来源；巨潮缩小窗口后仍不完整则拒绝父窗口。`pages`含失败探测页，`source_rows`仅计最终完整叶窗口的目录行，`window_splits`单列缩小次数。PDF失败可使运行partial，但独立合格结构化数据和完整目录仍可发布。

`--metadata-only`只归档目录，不声称PDF齐备。离线重放在同步命令追加`--offline`：按相同报告期、窗口与范围读取已完成收据，核验全部原始文件，不触网、不把旧档推进为最新版本。没有完整收据或归档损坏时明确失败；已成功独立窗口仍可重放。

## 数据与查询边界

- 四类标准化指标：预计归母净利润、扣非净利润、每股收益、营业收入；金额CNY元，EPS为CNY/股，同比为百分数。收入和营业总收入不同；源仅写“每股收益”时不认证为基本或稀释EPS。未明确归属范围保留`unspecified`。
- 预增／减亏等类型按指标保存，同公告不同类型不拆成不同公告。上下限、单侧、定性、真实零与缺项分别保留，不采用源中点字段，不自动改负数区间端点。
- `fundamental.earnings_guidance_observation`保存内容版本；`earnings_guidance_relation`保存唯一身份关联及有据审核事件，支持撤销。来源金额未变的重放不增加观测；语义诊断不重复堆积。
- 当前查询返回各来源完整窗口中的候选，已知撤回／修订关系与采集失败状态分别展示。唯一目录关联只标`linked`，不是金额已逐公司核验；尚无权威自动修订裁决，不把来源最新提示当公司完整修订链。
- 没有已存观测的公司返回`no_observation_stored`；查询不认证采集完成，须同时核对同步收据；这不是预测零，也不证明公司没有预告。查询中的取得与公告日期用于追溯，非严格历史PIT。

东财新归档平铺在`workspace/eastmoney/`，巨潮复用`workspace/cninfo/`，原始文件由`meta.artifact`登记；主库不存源BLOB。运行输出、范围、锁和验收放`workspace/derived/earnings-guidance/`。

独立版本化证据包导出及故事自动绑定尚未交付；本命令的JSON查询不替代已发布SQLite／原生API。预告转年度假设、构造预告TTM或净利润转EBIT均不自动执行，边界见[valuation TODO](../../valuation/TODO.md)。

## 季度标题与来源重解释

“第三季度业绩预告／快报”本身不能区分前三季度累计与7–9月单季。当前v3保留标题截止期及公告，开始日未知时标`unknown`／`partial`；不要从东财累计区间、公告标题或报告日替PDF猜起始日。明确日期及范围按原文人工引用，通用PDF期间解析尚未实现。

解释版本升级后，用相同范围及完整归档窗口显式`--offline`重建，再以同一命令重放及重开查询。核对当前公司/公告集合、金额、原始取得时间不变，正确区分新增解释版本与新增上游公告；旧观测、关联及源证据保留。不能删除旧事实、改公告日期或再次在线抓取来掩盖解释差异。
