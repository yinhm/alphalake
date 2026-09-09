# A 股自动化估值

目标是所有已发现的 A 股都有可解释的处理结论：完成条件估值、缺数据、缺政策或模型不适用。字段齐全不等于估值完成，不能为提高成功率将未知值补零。

## 财务就绪度扫描

```bash
alphalake valuation-readiness ./alphalake.duckdb \
  --period 2026-06-30 --as-of 2026-09-09T18:00:00Z > readiness.json
```

只读查询同一事务中的本地证券主数据和标准 ASOF/TTM 窗口，不读取 PDF 直算，不修改事实。分母包括本地已知、未退市的沪深北人民币股票，无标准事实或无唯一 TDX 身份也保留。ETF、指数、外币股票排除。证券身份有效日取信息截止的中国日期。

输出逐公司最新可用财报期、全部已审核字段窗口及源事实 ID、15 项核心字段缺项和汇总计数。核心检查覆盖收入、营业利润及利息/非经营损益、股本、现金、借款/债券/租赁/到期负债和少数权益；它是筛查契约，不是通用 DCF 的全部输入契约。未披露零仍缺失，核心齐全仍明确要求模型政策与市场输入评估。

`universe_scope` 明示：这是本地已发现集合，尚未与交易所完整名单对账；主数据名称/状态不是完整历史快照，不能据此声称无幸存者偏差的历史全市场覆盖。指定期间缺项不会静默退回上一份年报。

## 推进顺序

1. 建立真实证券分母、标准财务覆盖及缺项排行。
2. 将既有标准导出/政策校验/共享引擎接入批量任务，按公司隔离失败，保留成功版本及失败原因。
3. 增加普通非金融企业的通用、版本化假设规则和适用门槛；公司特有政策继续独立，金融企业不套普通 FCFF。
4. 根据实际阻塞补数据、行业映射和市场输入，做跨公司真实源验收，再扩增量调度。

扫描入口已实现；其余步骤按实际提交更新，不将计划记为完成。安克、茅台验收库只有两家公司，不能将其中的完整率当作全 A 股完整率。

## 独立刷新证券名单

```bash
alphalake sync-instruments ./alphalake.duckdb
```

复用既有分区主数据刷新与三节点重试，不下载日线。健康分区独立发布，分区失败使运行标为 partial 并返回非零退出码；失败诊断保留，不能把部分名单当完整市场。已有历史名单的保留和两次缺失确认规则不变。

## 批量运行已有审核政策

在 `valuation/backend` 下执行（使用已安装后端依赖的 Python）：

```bash
python -m tools.batch_valuate_alphalake /absolute/path/alphalake.duckdb \
  --period 2026-06-30 --as-of 2026-09-09T18:00:00Z \
  --policy batch-policy.json --output-dir ./data/batch_runs
```

`batch-policy.json` 包含 `policy_version`、`review_note`、`assignments`。`assignments` 按六位代码索引，每项包含现有完整 `policy` 和可选 `wacc_binding`，与单公司 API 契约一致。可将既有示例政策装入任务：

```python
import json
from pathlib import Path
policy = dict(policy_version='reviewed-2026H1-v1', review_note='仅已审核双公司，其他公司不外推', assignments={})
for code, name in [('300866','anker-2026H1-revised.json'), ('600519','moutai-2026H1-central.json')]:
    policy['assignments'][code] = dict(policy=json.loads(Path('../examples', name).read_text()))
Path('batch-policy.json').write_text(json.dumps(policy, ensure_ascii=False, indent=2))
```

批量命令直接调用与 HTTP 相同的 `evaluate`，无需启动 API 服务。先扫描本地分母，再逐证券导出同一请求期间/信息截止的标准数据；每份证券导出有独立事务，整批不是跨证券/跨库原子快照，应在同步完成后运行。

缺政策、缺项、错误身份/政策和执行失败分别列示。单公司成功结果继续保存在 `ALPHALAKE_VALUATION_RUN_DIR`（默认后端 `data/alphalake_runs`），按输入/政策/引擎版本寻址；每次批量尝试另存独立 JSON，保留失败记录。重跑会重新检查数据并重试失败，成功内容不覆盖；当前仍重新执行引擎，尚无计算缓存或守护调度进程。

这一步自动化执行已有政策，尚不自动生成全市场政策。双公司实际数据库试跑成功、默认测试覆盖重放、缺附注隔离和身份错配；它们不代表新增行业已经验收。
