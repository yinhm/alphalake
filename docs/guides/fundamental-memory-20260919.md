# 大任务内存隔离

本机曾因全量财务重放触发OOM，同一scope内的bash和Codex一并退出。DuckDB预算不等于进程硬上限；大库重放、导出和全套测试必须串行运行于独立systemd服务，不能从交互终端直接启动。

## 执行约束

- 当前进程硬限额1GiB，`MemoryHigh=896M`、`MemorySwapMax=0`；先检查系统空闲内存，不能仅因单任务有上限就并发启动。
- 全量财务重建使用DuckDB384MiB、单线程；Go使用`GOMEMLIMIT=128MiB`、`GOMAXPROCS=1`。限额不足先分析结构与峰值，禁止盲目加内存、缩批或重试。
- 先小批验收，再执行固定范围任务；记录进程冷暖、耗时、RSS、输入版本和失败结果，门槛见[性能约束](financial-storage-redesign-20260925.md)。

以下示例只演示隔离方式，实际操作先选定同步、物化或发布范围；不能把全量重放当作日常必做步骤。

```bash
systemd-run --no-block --unit=alphalake-materialize \
  --property=Type=oneshot --property=RemainAfterExit=yes \
  --property=WorkingDirectory=/root/alphalake \
  --property=MemoryMax=1G --property=MemoryHigh=896M \
  --property=MemorySwapMax=0 \
  --setenv=GOMEMLIMIT=128MiB --setenv=GOMAXPROCS=1 \
  --setenv=ALPHALAKE_DUCKDB_MEMORY_LIMIT=384MiB \
  --setenv=DUCKDB_JE_MALLOC_CONF=dirty_decay_ms:0,muzzy_decay_ms:0 \
  --setenv=ALPHALAKE_DUCKDB_THREADS=1 \
  /root/alphalake/alphalake materialize-fundamentals workspace/alphalake.duckdb
systemctl show alphalake-materialize \
  -p ControlGroup -p MemoryMax -p MemoryHigh -p MemorySwapMax \
  -p ActiveState -p Result -p ExecMainStatus -p MemoryPeak
journalctl -u alphalake-materialize --no-pager -n 60
```

必须核验服务确实处于独立ControlGroup且`MemoryMax=1073741824`。异常退出先查单元及内核日志，保留失败库/WAL；禁止删除WAL冒充恢复，或在原因未明时原参数重启。

本机全量宽表重建还需将DuckDB原生分配器的闲置页及时归还系统（上述`DUCKDB_JE_MALLOC_CONF`）；该变量见[DuckDB官方说明](https://duckdb.org/docs/current/internals/jemalloc)。这是内存回收配置，不增加预算、不缩批或重连；实际耗时与RSS仍以同范围验收为准。

批量写入仍按包提交WAL；当前连接将自动checkpoint阈值设为1GiB，避免默认16MiB频繁重压宽表，正常关闭时由DuckDB落盘。该阈值是日志大小而非内存预算；异常退出保留WAL恢复，不能删除。设置依据见[官方配置](https://duckdb.org/docs/current/configuration/overview)。

## 测试环境

全套测试同样使用独立服务，并与数据任务串行。本机Go缓存使用`GOPATH=/root/go`、`GOCACHE=/root/.cache/go-build`；Python回归使用项目`.venv/bin/python`，必要时设置`ALPHALAKE_TEST_PYTHON=/root/alphalake/.venv/bin/python`和`PYTHONPATH=/root/alphalake/valuation/backend`。测试应取消`ALPHALAKE_WORKSPACE`覆盖，避免临时样本误指向主workspace。依赖缺失或skip如实报告。

早期长表实现、1.5GiB试验及OOM测量见[历史记录](../history/fundamental-memory-20260919.md)，不是当前执行预算。
