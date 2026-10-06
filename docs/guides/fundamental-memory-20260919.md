# 大任务内存隔离

本机曾因全量财务重放触发OOM，同一scope内的bash和Codex一并退出。DuckDB预算不等于进程硬上限；大库重放、导出和全套测试必须串行运行于独立systemd服务，不能从交互终端直接启动。

## 执行约束

- 当前整个服务组硬限额1GiB，宽表重建及多阶段导出的已验证配置为`MemoryHigh=960M`、`MemorySwapMax=0`；先检查系统空闲内存，不能仅因单任务有上限就并发启动。
- 上述任务使用DuckDB组件预算512MiB、单线程，为Python编排、Go、原生分配和结果保留空间；不能把DuckDB预算设成整个服务的1GiB硬上限。Go使用`GOMEMLIMIT=512MiB`、`GOMAXPROCS=1`。两个软预算不保证RSS，也不能相加推算整组峰值；须核验实际服务资源。10月6日组件1GiB／768MiB均曾触发持续内存回收限速，512MiB接续成功，失败及WAL恢复见[补链验收](../history/cyclical-capital-inputs-20261006.md#全库存储验收)。限额不足先分析结构与峰值，禁止盲目加内存、缩批或重试。
- 先小批验收，再执行固定范围任务；记录进程冷暖、耗时、RSS、输入版本和失败结果，门槛见[性能约束](financial-storage-redesign-20260925.md)。

以下示例只演示隔离方式，实际操作先选定同步、物化或发布范围；不能把全量重放当作日常必做步骤。

```bash
ALPHALAKE_REPO="$PWD"  # 在项目根目录执行
systemd-run --no-block --unit=alphalake-materialize \
  --property=Type=oneshot --property=RemainAfterExit=yes \
  --property=WorkingDirectory="$ALPHALAKE_REPO" \
  --property=MemoryMax=1G --property=MemoryHigh=960M \
  --property=MemorySwapMax=0 \
  --setenv=GOPATH="$(go env GOPATH)" --setenv=GOCACHE="$(go env GOCACHE)" \
  --setenv=GOMEMLIMIT=512MiB --setenv=GOMAXPROCS=1 \
  --setenv=ALPHALAKE_DUCKDB_MEMORY_LIMIT=512MiB \
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

默认及全局测试范围以[测试约定](testing.md)为准。Go全局使用`go test -tags=full -timeout 30m ./...`，Python全局加`--full`；默认命令不再启动重型真实源链建库或全量PDF验收。`-timeout`只扩展包级超时，不放宽资源限额。Go缓存及解释器须显式传入，测试取消`ALPHALAKE_WORKSPACE`以免读写主workspace；跳过和依赖缺失如实报告。

在项目根目录，以已安装Go环境启动隔离服务；默认后端示例：

```bash
ALPHALAKE_REPO="$PWD"
systemd-run --unit=alphalake-backend-test \
  --property=WorkingDirectory="$ALPHALAKE_REPO" \
  --property=MemoryMax=1G --property=MemoryHigh=960M \
  --property=MemorySwapMax=0 --property=OOMPolicy=stop \
  --setenv=PYTHONPATH="$ALPHALAKE_REPO/valuation/backend" \
  --setenv=GOPATH="$(go env GOPATH)" --setenv=GOCACHE="$(go env GOCACHE)" \
  --setenv=GOMEMLIMIT=512MiB --setenv=GOMAXPROCS=1 \
  --setenv=ALPHALAKE_DUCKDB_MEMORY_LIMIT=512MiB \
  --setenv=ALPHALAKE_DUCKDB_THREADS=1 \
  /usr/bin/env -u ALPHALAKE_WORKSPACE \
  "$ALPHALAKE_REPO/.venv/bin/python" -m pytest -q valuation/backend/tests --durations=30
```

显式全局在末尾加`--full`；Go使用相同隔离配置、相应命令和`ALPHALAKE_TEST_PYTHON="$ALPHALAKE_REPO/.venv/bin/python"`。本地未请求全局时须取消意外继承的`CI`变量；GitHub Actions显式执行全局。核对systemd退出码、硬限额和单元日志，不能仅看测试进程最后一行。

9月30日先前优化及测量移至[历史验收](../history/backend-test-performance-20260930.md)，其中默认全量与研究回放范围已由当前分层取代。早期长表实现与OOM见[历史记录](../history/fundamental-memory-20260919.md)。
