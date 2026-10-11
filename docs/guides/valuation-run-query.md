# 历史估值查询

`tools.list_valuation_runs`只读已发布的估值JSON，供人和Skill发现run ID；不查询数据库、不计算估值、不写运行文件。沿用现有运行保存格式，无新表或依赖。

在`valuation/backend`中用已安装后端依赖的Python执行：

```bash
python -m tools.list_valuation_runs 300866 \
  --run-dir "$ALPHALAKE_RUN_DIR" \
  --model nonfinancial-history-fcff-v1 \
  --period 2026-06-30 --as-of 2026-09-10T12:00:00+00:00 \
  --latest-per-model --limit 50 --offset 0 > history.json
```

`--run-dir`、`--model`可重复；模型为精确`policy_id`，报告期精确匹配，信息截止取小于等于指定时点。省略目录时使用`ALPHALAKE_VALUATION_RUN_DIR`或`workspace/derived/valuation-runs`；不自动寻找workspace中的隔离库结果。省略模型/期间/截止表示不加相应筛选。

## 结构与选择规则

契约为`alphalake-run-query-v1`。退出0表示查询完成（`ok`或`no_matches`），退出2为`partial`，退出1为`query_rejected`；参数语法错误沿用argparse。

- `results`含run ID、模型/情景、报告期/信息截止、归档每股值与CNY单位、WACC小数比例、资本口径、引擎版本、请求哈希和文件路径/原字节SHA。
- `matched_count`是筛选后、取最新前的去重记录数；`result_count`是取最新后的总数。默认每页50，最多500；用`has_more`和`offset`续页。
- `--latest-per-model`按各`policy_id`的最大`information_as_of`保留全部并列，等价时区表示算同一时刻。历史格式没有可靠计算创建时间，不使用mtime或ID大小推断新旧。
- `model_groups`给出最新截止、并列数及唯一时才有的`unique_latest_run_id`。元数据可能在其他分页中，先取得对应行再操作；模型名相同并不代表情景或资本口径相同。
- 多目录同ID、内容相同的副本合并为一行、多条`locations`；同ID内容冲突则排除该ID。不同ID即使价格相同也保留。
- 损坏JSON、非法ID、缺失目录等进入`issues`，最多展示100项并保留总数与省略数。任一问题都会返回`partial`、`latest_selection_complete=false`，并清空所有组的唯一最新ID，防止跳过未知记录后宣称最新。

验证范围为`request_engine_hash_only_not_report_replay`：验证请求/引擎与ID一致，摘要金额检查有限数值，但**不证明保存报告正确**。需要比较时，将查询得到的ID与`locations[].path`父目录交给[比较入口](valuation-comparison.md)，该入口会重算核验完整报告。

仅顺序扫描指定目录第一层JSON，非原子快照；查询期间若有并发发布，需重新查询。耗时随目录历史增长，目前不建索引；“最新”仅限这些目录的信息截止，不代表最新市场数据，也不表示当前政策已批准。

## 过期运行退役

用户已授权清理过期派生记录。`no_matches`只表示当前保留目录无匹配，不表示从未估值；先查`derived/cleanup-audit/`的退役清单，不为找ID自动重新估值。10月11日退役的3,171份记录保留于`derived/archives/retired-valuation-inputs.jsonl.gz`，包含typed输入、显式政策、审阅、结果和原文件摘要；重复源事实及参考绑定正文已撤除，**不再具备原run ID逐字节重放条件，也不是本查询／比较入口的运行JSON**。

近期故事归档保留完整必要成员，使用时按退役收据在隔离临时workspace解包，并按`pdf_restore`恢复源引用及使用原提交；不能把其中的故事ID传给旧运行查询。其他已结束研究只留条件／摘要时，不承诺完整响应复验。保留范围见[派生清理记录](../history/derived-retirement-20261011.md)，规则见[workspace布局](workspace-layout.md#清理与迁移)。

历史样本数值、运行标识及测试收据见[验收记录](../history/valuation-run-query.md)；当前交付数量统一见[项目状态](../implementation-status.md)。
