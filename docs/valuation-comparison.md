# 估值运行比较

`tools.compare_valuations`按两个run ID读取已有请求，分组输出差异，并用当前共享引擎重算核验两端。它不访问数据库或网络，不调用会保存新运行的`evaluate`，不覆盖原始文件。

不知道run ID时，先用[历史查询](valuation-run-query.md)按公司和口径取得ID及实际目录；同一信息截止的并列运行不能自动任选。

在`valuation/backend`下使用已安装依赖的Python：

```bash
python -m tools.compare_valuations BEFORE_RUN_ID AFTER_RUN_ID \
  --run-dir /absolute/alphalake_runs > comparison.json
```

不同运行目录可加`--after-run-dir /absolute/other_runs`；默认运行目录沿用`ALPHALAKE_VALUATION_RUN_DIR`或后端`data/alphalake_runs`。参数只能是64位小写十六进制run ID，不接受任意文件路径代替ID。退出0表示比较完成，**不代表已完成因果归因**；退出2输出`comparison_rejected`及原因。语法错误沿用argparse。

## 输出与校验

JSON契约为`alphalake-valuation-comparison-v1`：

| 字段 | 内容 |
| --- | --- |
| `before` / `after` | 两端run ID、记录的引擎/运行时、证券代码及标准ID、报告期/信息截止、模型/情景、股本依据、每股值与WACC。 |
| `value_difference` | 后值减前值，CNY/share；十进制字符串由保存的模型浮点输出转换计算，不代表恢复财报源精度。无每股值时为空。 |
| `changes` | 财务与证据、时点/身份、WACC与市场输入、资本效率参考、股权/股本政策、模型与政策等分组。每项包含JSON Pointer路径和前后值，缺键与null分开。 |
| `derived` | 当前重算的逐年现金流是否相同、股权桥接输入是否相同，以及两端完整股权桥接结果；不是分项贡献相加表。 |
| `verification` | 当前重算引擎/运行时、历史引擎是否变化及保存输入相对当前规范化输入的差异。 |
| `attribution` | `verified_wacc_only`或`not_attributed`；只有前者输出WACC每股贡献。 |
| `evidence` | 两个原始运行文件的绝对路径与原字节SHA。 |

每个分组默认最多展示100项，`--max-changes`可设1–10000。总数`count`与省略数`omitted_count`始终保留；长对象/文本用内容摘要代替全文，按路径回原文件查看。数组按保存顺序比较，重新排序或不同库中的事实ID/公告ID也会产生差异；`financial_data_and_evidence`不等于财务金额发生变化的条数。

读取时验证run ID与请求/引擎内容哈希；随后用当前适配器重新校验请求，从头执行共享引擎，要求**完整报告与归档精确相等**。原报告被篡改、请求缺项、非法源位，或当前引擎不能复现历史报告时，拒绝比较和归因，不用重算值替换历史值。哈希不提供外部来源认证，重算也不是第二条财报取数证据链。

若完整报告一致，但当前规范化输入与历史保存输入不同，仍可展示比较，并单列`normalization_changes`；这种情况下不授予单因素归因。历史引擎不同本身不阻止比较，因为两端都已由同一个当前引擎复现；这不证明任意旧运行都兼容当前版本。

## 限定的WACC贡献

当前仅对两端同为`nonfinancial-history-fcff-v1`的通用模型判断WACC单因素，要求：

- 两份请求除`policy.wacc`与`wacc_binding`外完全一致，包含财务数据、信息截止、政策其他字段及资本效率参考。
- 两端当前规范化输入分别与归档一致，完整报告均重算一致。
- 两端逐年收入、EBIT、再投资、FCFF和股权桥接输入相同；WACC确有变化。

此时两端重算就是保持其余输入不变的受控对照，`wacc_contribution_per_share`等于后值减前值。它衡量该模型中的折现率配置效应，不是现实价格因果效应。

其他情况都只提供差异清单和总差额，不做任意顺序的混合输入替换，不为多因素交互编造精确贡献。专项模型内部WACC单因素、顺序分解或Shapley归因尚未实现。同证券代码但标准ID不同时保留双方ID；跨库/生命周期身份仍需核实，不能据代码相同证明同一发行人。

## 安克真实验收（2026-09-10）

| 对照 | 每股差额 | 判断 |
| --- | ---: | --- |
| 通用固定10% WACC的84.4904605659 → 行业WACC的126.1378483323 | +41.6473877664元 | `verified_wacc_only`；完整请求除WACC配置外相同，输入/现金流/桥接验证通过。 |
| 通用126.1378483323 → 专项173.7471143725 | +47.6092660402元 | `not_attributed`；财务证据、截止时点、模型、资本效率、风险与股权政策均有变化。 |

第一组虽然历史引擎不同，两端规范化输入和完整报告均精确复现。第二组专项输入的`prepared_ttm.provenance.alphalake_snapshot`在当前规范化时改变，完整报告仍精确相同；摘要差异单列，不改历史文件、不当财务金额变化。

原始run ID及产物哈希见[验收摘要](acceptance/valuation-comparison-20260910.json)，完整输出在本地`workspace/valuation-comparison-20260910/`。回归另覆盖多因素不归因、相同运行差额0、缺键/null、数组差异、规范化变化、跨证券拒绝、路径穿越拒绝、请求/报告篡改及原文件不变。真实金额来自已保存标准链，不新增PDF语义审核或估值覆盖率。

本轮Python全套240通过、4项既有外部环境跳过；Go全套、构建、vet、Skill校验、文档链接与`git diff --check`通过。两组原始运行文件在验收后SHA保持不变；未新增依赖、迁移或修改估值公式。
