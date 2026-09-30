# 大任务内存隔离

本机曾因全量财务重放触发OOM，同一scope内的bash和Codex一并退出。DuckDB预算不等于进程硬上限；大库重放、导出和全套测试必须串行运行于独立systemd服务，不能从交互终端直接启动。

## 执行约束

- 当前进程硬限额1GiB，`MemoryHigh=896M`、`MemorySwapMax=0`；先检查系统空闲内存，不能仅因单任务有上限就并发启动。
- 当前统一DuckDB预算1GiB，单线程；Go使用`GOMEMLIMIT=512MiB`、`GOMAXPROCS=1`。`GOMEMLIMIT`是Go垃圾回收软预算，不是RSS上限，也不覆盖DuckDB原生分配；整个服务组仍受1GiB硬上限约束。限额不足先分析结构与峰值，禁止盲目加内存、缩批或重试。
- 先小批验收，再执行固定范围任务；记录进程冷暖、耗时、RSS、输入版本和失败结果，门槛见[性能约束](financial-storage-redesign-20260925.md)。

以下示例只演示隔离方式，实际操作先选定同步、物化或发布范围；不能把全量重放当作日常必做步骤。

```bash
ALPHALAKE_REPO="$PWD"  # 在项目根目录执行
systemd-run --no-block --unit=alphalake-materialize \
  --property=Type=oneshot --property=RemainAfterExit=yes \
  --property=WorkingDirectory="$ALPHALAKE_REPO" \
  --property=MemoryMax=1G --property=MemoryHigh=896M \
  --property=MemorySwapMax=0 \
  --setenv=GOPATH="$(go env GOPATH)" --setenv=GOCACHE="$(go env GOCACHE)" \
  --setenv=GOMEMLIMIT=512MiB --setenv=GOMAXPROCS=1 \
  --setenv=ALPHALAKE_DUCKDB_MEMORY_LIMIT=1GiB \
  --setenv=DUCKDB_JE_MALLOC_CONF=dirty_decay_ms:0,muzzy_decay_ms:0 \
  --setenv=ALPHALAKE_DUCKDB_THREADS=1 \
  "$ALPHALAKE_REPO/alphalake" materialize-fundamentals workspace/alphalake.duckdb
systemctl show alphalake-materialize \
  -p ControlGroup -p MemoryMax -p MemoryHigh -p MemorySwapMax \
  -p ActiveState -p Result -p ExecMainStatus -p MemoryPeak
journalctl -u alphalake-materialize --no-pager -n 60
```

必须核验服务确实处于独立ControlGroup且`MemoryMax=1073741824`。异常退出先查单元及内核日志，保留失败库/WAL；禁止删除WAL冒充恢复，或在原因未明时原参数重启。

本机全量宽表重建还需将DuckDB原生分配器的闲置页及时归还系统（上述`DUCKDB_JE_MALLOC_CONF`）；该变量见[DuckDB官方说明](https://duckdb.org/docs/current/internals/jemalloc)。这是内存回收配置，不增加预算、不缩批或重连；实际耗时与RSS仍以同范围验收为准。

批量写入仍按包提交WAL；当前连接将自动checkpoint阈值设为1GiB，避免默认16MiB频繁重压宽表，正常关闭时由DuckDB落盘。该阈值是日志大小而非内存预算；异常退出保留WAL恢复，不能删除。设置依据见[官方配置](https://duckdb.org/docs/current/configuration/overview)。

## 测试环境

全套测试同样使用独立服务，并与数据任务串行。Go缓存位置通过`go env GOPATH`、`go env GOCACHE`取得并显式传入服务；Python回归使用项目`.venv/bin/python`，必要时设置`ALPHALAKE_TEST_PYTHON="$PWD/.venv/bin/python"`和`PYTHONPATH="$PWD/valuation/backend"`（在项目根目录执行）。测试应取消`ALPHALAKE_WORKSPACE`覆盖，避免临时样本误指向主workspace。依赖缺失或skip如实报告。

日常后端开发先执行下列快速范围，再补本轮修改对应的测试文件；提交前涉及来源、研究统计或广泛行为时执行完整组。快速范围覆盖引擎、原生接口/输入、发布和调度，不包含Go真实源链或全部研究回放；不能称完整测试。pytest默认仍执行完整组，CI没有删掉对应验收。

在项目根目录，以已安装Go环境启动隔离服务：

```bash
ALPHALAKE_REPO="$PWD"
systemd-run --unit=alphalake-backend-fast \
  --property=WorkingDirectory="$ALPHALAKE_REPO" \
  --property=MemoryMax=1G --property=MemoryHigh=896M \
  --property=MemorySwapMax=0 --property=OOMPolicy=stop \
  --setenv=PYTHONPATH="$ALPHALAKE_REPO/valuation/backend" \
  --setenv=GOPATH="$(go env GOPATH)" --setenv=GOCACHE="$(go env GOCACHE)" \
  --setenv=GOMEMLIMIT=512MiB --setenv=GOMAXPROCS=1 \
  --setenv=ALPHALAKE_DUCKDB_MEMORY_LIMIT=1GiB \
  --setenv=ALPHALAKE_DUCKDB_THREADS=1 \
  /usr/bin/env -u ALPHALAKE_WORKSPACE \
  "$ALPHALAKE_REPO/.venv/bin/python" -m pytest -q \
  valuation/backend/tests/engine valuation/backend/tests/test_native_*.py \
  valuation/backend/tests/test_alphalake_refresh.py \
  valuation/backend/tests/test_standard_financial_input.py \
  valuation/backend/tests/test_pdf_evidence.py valuation/backend/tests/test_research_metrics.py
```

完整组使用相同隔离配置，将`valuation/backend/tests/engine`起的测试范围替换为`valuation/backend/tests --durations=30`。核对systemd退出码；测试文件按修改范围显式补充，不能只依快速组筛选成功。测量记录位于`workspace/derived/backend-test-performance/`。

### 后端回归耗时基线（2026-09-30）

同机、暖缓存、上述统一资源配置，串行单次测量：

| 范围 | 结果 | 耗时 |
|---|---|---:|
| 优化前完整组 | 631通过、4跳过 | 702.19秒 |
| 优化后完整组 | 637通过、4跳过 | 577.88秒 |
| 日常快速范围 | 262通过、4跳过 | 13.80秒 |

完整组原635项全部保留，新增6项；耗时减少17.7%，前后服务组峰值均896.5MiB。不是多次中位数或冷缓存基准。完整组仍有11条既有警告，快速组3条；两组的跳过集合相同，保留原边界。

优化包括：每次重验PDF哈希、有界复用最多16份`pdftotext`文本，提取函数替换会失效；同一报表页只提取一次；研究预测先投影公司索引，保留重复记录及原分母；留一公司收入MAE采用精确分组累加后转float，与原`statistics.mean`一致，不计算未消费的其他指标。冻结结果和篡改拒绝回归均保留，未改估值引擎、方法或阈值。来源CLI各自进程仍首次读取真实原文，不持久化缓存核验结论。

CI删除第三次重复标准链执行，安克和茅台适配脚本仍各自先核验完整链，没有skip开关；本地检查了工作流语法及保留的完整后端步骤，不冒称远端CI已经运行。剩余完整组主要成本为真实Go建库/源链及研究回放，日常局部修改不要求重复这些独立来源验收。

早期长表实现、1.5GiB试验及OOM测量见[历史记录](../history/fundamental-memory-20260919.md)，不是当前执行预算。
