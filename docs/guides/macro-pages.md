# 宏观阅读与历史发布契约

2026-10-21用户批准独立只读宏观页面。`/macro`展示最新已发布阅读快照，`/macro/history`列已发布期次，`/macro/:id`读取不可变历史页；公司估值、默认和计算引擎保持原契约。

## 数据边界

- 权威事实仍为`workspace/alphalake.duckdb`的`macro`域，来源及期间见[宏观数据指南](macro-data.md)。Go `export-macro`在同一个只读事务中取得系列与当前来源版本的历史观测，共享来源元数据按发布ID保存一次。
- Python `tools.macro_pages`只编排既有Go导出、校验点评并发布阅读文件。网页请求只读`workspace/macro`，不打开DuckDB、调用Go、抓取上游或修改财务SQLite。
- `workspace/macro/editorial/`放中文点评稿；`publications/<id>.json`放不可变消费快照；`index.json`原子登记最新期及历史摘要与哈希。此处是用户要求保留的历史产品，不是源文件副本；原文仍在来源目录，不复制原HTML或主库。
- 发布锁、待登记索引及运行收据放`workspace/derived/macro-pages`，不散落workspace顶层或阅读目录；索引原子替换失败时保留原当前版本。
- 一个期次绑定同版导出、点评、内容摘要、生成及发布时间。重复发布相同内容复用原期次；相同ID不同内容拒绝。索引写入失败不改变旧最新页；并发发布互斥，未登记文件不作为可读页面。

期次ID使用小写字母、数字和连字符，推荐`cn-macro-YYYYMMDD`；`history`为页面路由保留名，不可发布。

## 阅读内容

最新期包含中文摘要、重要指标卡、GDP／PMI／生产消费／信用趋势、价格及投资横截面比较、数据点评、完整指标搜索及原文链接。窄屏单列，图表用现有Recharts；不用不同比例的双轴、三维图或曲线平滑制造趋势。

金额按明确量纲展示；比率乘100显示%，PMI保持指数点，百分点保持原值。期末余额、当月及累计单列，图表不得混用同比／环比或实际／名义。未知不补零、不前向填值；日期轴保留真实间隔，只有一期就标示当期观测。可比前值必须期间相邻且定义相同；“上期”差异是百分点或指数点，不称金额增长率。前端只转换显示尺度，不生成经营预测、TTM或WACC。

每组点评区分观察与解读，声明作者及审核时间，绑定实际快照中的系列、期间和精确值；证据不匹配拒绝发布。点评不是来源事实或自动选参器。来源说明列最新统计期、发布时间／未知、首次取得及来源版本；生成日期不冒充所有指标最新统计期。来源采集失败不由阅读快照掩盖，实际同步状态继续看数据侧收据。

点评稿为JSON：`title`、`summary`、`author`及带时区的`reviewed_at`；`sections`每项包含`title`、Markdown `body`和至少一项`evidence`；证据使用标准`series_code`、`period_end`和精确小数字符串`value`（未知为null）；`limitations`列来源及解读边界。正文禁任意HTML和图片，外链只允许HTTPS。文字结论仍须人工核对，机器绑定仅证明引用的期间和值与该快照一致。

**历史曲线与历史发布是两回事**：现有原文自带的过去期间仍属本次取得的修订版本；历史页仅从实际发布起积累，不回造早年的点评或声称严格PIT。后续修订发布新ID，已发页面不跟随主库改写。

## 入口

在项目根目录、独立1GiB服务中串行执行；所有参数显式指定，环境沿[资源约定](fundamental-memory-20260919.md)：

```bash
# 查看导出契约（stdout JSON）；不写主库。
./alphalake export-macro workspace/alphalake.duckdb

# 先维护点评稿；publish只取已有主库，不附带在线刷新。
PYTHONPATH=valuation/backend .venv/bin/python -m tools.macro_pages publish \
  --database workspace/alphalake.duckdb --id cn-macro-20261021 \
  --commentary workspace/macro/editorial/cn-macro-20261021.json

# 完整核验登记、数据关系、点评绑定及哈希。
PYTHONPATH=valuation/backend .venv/bin/python -m tools.macro_pages validate \
  --id cn-macro-20261021
```

API：`GET /api/macro`返回已发布目录；`GET /api/macro/latest`取得最新登记期；`GET /api/macro/publications/{id}`返回指定不可变期次。返回内容版本及ETag；缺目录为空、未知期次404、坏登记／哈希／数据关系错误显式拒绝，不静默改读旧版。读取无副作用。

验收包括真实主库导出与发布、零／负值／NULL、前值及期间缺口、空目录、篡改／路径越界拒绝、历史页冻结、刷新直接访问、窄屏及键盘／图表数据表。临时测试用独立workspace，不继承真实归档，不生成假历史期次。
