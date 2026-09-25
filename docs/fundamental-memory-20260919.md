# 全字段财务物化的内存修复

此前默认重放一次性生成全部字段的候选/拒绝表、事实表，并按每条源事实检查映射重叠。主库19,310,544条源事实、281个标准字段下曾两次触发全机OOM。DuckDB的内存参数不是进程RSS硬上限；事故与恢复见[ADR020](decisions/020-official-statements-and-snapshots.md)。

原修复复用SQL和事务，按每批六个来源字段限制工作集。2026-09-25补充目录接入后，六字段合并触及DuckDB 256MiB查询上限（独立服务内失败，没有系统OOM）；当前缩至每批三个字段，并显式限定合并/计数的既有事实范围；股东数据批次仍超过256MiB，256MiB和384MiB均未完成整批接入；schema51最终采用512MiB查询预算、维持1GiB服务硬限额，65字段发布与全字段零变更重放均通过，后续验收见[补充目录记录](tdx-supplementary-fields-20260925.md)。字段清单从有映射的源事实及既有标准事实动态取得，不增加业务白名单；已经删除映射的旧事实仍参与清理。全部批次共用一次事务、一个运行，任何后续批次失败均回滚前批事实及诊断，不按批提前提交。原单位、期间、拒绝规则与源精度不变。

回归覆盖跨批末端映射冲突导致整体回滚，以及删映射后旧事实仍撤除。仅限制中间工作集，不去掉诊断、不放宽映射审核、不将拒绝改成零。

资源保护采用独立systemd服务，硬内存上限1GiB、禁用swap、单线程，旧281字段验收时DuckDB限256MiB，当前346字段使用512MiB；大库命令、构建和测试串行执行。旧实现受控基线运行约101秒，内存峰值640.5MiB并持续触发软阈值，在生成候选/拒绝表阶段主动取消；该次没有OOM，也不是完成时间基准。

修复后默认全字段命令首次完整结束（运行5437），耗时256秒，systemd峰值896.5MiB（包含文件缓存）、swap峰值0；9,152,443候选、4,403,786可物化、4,748,657拒绝，新增1、更新0、删除0。状态partial表示保留歧义和缺项，不是执行失败。

新增项是安克2024年末其他应收款126,612,168元：既有已审核映射已将期间扩至2024年末，主库本次全量重算才补出该条；来源数值和单位原样规范化。这不是本轮124项新映射的变化，也不是内存优化改变财务值。原21批只重算新增映射，不能代替默认全字段验收。

第二次默认全字段重放（运行5438）完整通过，耗时约253秒，服务内存峰值896.4MiB、swap峰值0；候选/拒绝分母相同，新增、更新、删除均为0。两次均在硬限额内结束，无OOM。[验收收据](acceptance/fundamental-memory-20260919.json)保留新增项及运行记录；全套Go测试、构建、vet均通过；测试/构建/查询串行服务内存峰值857.1MiB、swap峰值0。安克、茅台、苏泊尔2026H1三表JSON与修复前逐项一致；本轮未重新计算DCF。

已编译程序的受控运行示例（本机systemd，使用独立系统服务）：

```bash
systemd-run --unit=alphalake-materialize \
  --property=WorkingDirectory=/root/alphalake \
  --property=MemoryMax=1G --property=MemoryHigh=896M \
  --property=MemorySwapMax=0 --property=OOMPolicy=stop \
  --setenv=ALPHALAKE_DUCKDB_MEMORY_LIMIT=512MiB \
  --setenv=ALPHALAKE_DUCKDB_THREADS=1 --setenv=GOMEMLIMIT=128MiB \
  /tmp/alphalake materialize-fundamentals workspace/auto-valuation-20260909/market.duckdb
systemctl show alphalake-materialize -p ControlGroup -p MemoryMax -p ActiveState -p Result
journalctl -u alphalake-materialize --no-pager
```

服务启动成功不等于任务成功；验收以退出状态、运行记录和事实增改删为准。若首次失败，先核对日志再决定下一步，不提高硬上限盲目重试。

2026-09-25发现另一个边界：最新财务包已校验归档，但原始入库进程触及1GiB服务硬限额，被cgroup OOM终止；正常重开数据库恢复，未删除WAL。这里的标准物化批次优化不覆盖源入库的整包暂存。后续须先定位并降低该阶段内存峰值，再以同一归档包验证原子性，不能声称全同步链已满足1GiB预算；详情见[本轮记录](tdx-supplementary-fields-20260925.md)。
