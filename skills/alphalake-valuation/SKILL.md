---
name: alphalake-valuation
description: 使用 AlphaLake 已有数据库和显式政策进行 A 股公司条件估值，解释专项与行业候选、缺项和来源证据。适用于公司估值、历史估值发现、已有估值结果解读、两次估值比较和口径核对；不用于一般股价预测、仓库开发或全市场同步维护。
---

# AlphaLake 公司估值

调用项目已有 `tools.company_valuation`，把结构化结果解释给用户。计算、身份/单位/时点校验与政策准入由程序执行；不要在Skill中重写DCF、补数或另选数据源。

## 准备调用

- 定位AlphaLake仓库：优先使用会话已知路径或`ALPHALAKE_ROOT`，否则检查当前目录及父目录的`valuation/backend/tools/company_valuation.py`。安装Skill的目录不等于仓库目录。
- 首次使用读取仓库内`docs/company-valuation-entry.md`；需要准备行业政策或理解完整度时，再读`docs/a-share-automation-acceptance-20260910.md`。文档中的本机路径、报告期、估值数字是验收示例，不是默认配置。
- 从用户请求和已确认配置取得：六位证券代码、财务库、报告期、带时区信息截止、已安装后端依赖的Python、一个或多个`BatchPolicy`文件；参考驱动政策还需匹配参考包或参考库。公司名称、代码身份、数据库范围不明确时先核实，不猜代码或按价格挑库。
- 复用会话已经授权和确认的输入；必要参数确实缺失时只询问缺失部分，不重复请求许可。不要把单公司Policy JSON直接传给需要BatchPolicy的`--policy`；包装方法见上述入口文档。
- 普通公司估值请求只计算并保存估值运行；不顺便同步全市场、升级/替换数据库或修改政策以消除拒绝。当前入口会计算所有候选并写入运行文件，并非只读历史检索。

## 执行

下面变量由已核实配置设置，使用绝对路径；在仓库后端目录调用，避免安装位置或shell当前目录影响导入：

```bash
cd "$ALPHALAKE_ROOT/valuation/backend"
"$ALPHALAKE_PYTHON" -m tools.company_valuation "$ALPHALAKE_DB" "$ALPHALAKE_CODE" \
  --period "$ALPHALAKE_PERIOD" --as-of "$ALPHALAKE_ASOF" \
  --policy "$ALPHALAKE_POLICY" --alphalake "$ALPHALAKE_ROOT/alphalake" \
  > "$ALPHALAKE_RESULT"
```

需要时添加重复的`--policy /absolute/other-policy.json`及`--reference-database /absolute/references.duckdb`。内嵌参考包与参考库参数互斥，按实际配置选择；不要静默删除绑定。`--select`接受传入配置的`policy_version`，仅在用户已明确指定或已确认的选择规则适用时使用，不按候选价格高低选择。

保留退出码，并在非零退出后仍读取输出JSON及stderr。大库仍需全分母扫描，依据真实进度和入口超时排查，不并发反复重跑同一查询。解释器/二进制/依赖缺失应报告具体环境缺项，不能改用样本脚本算一个数。

## 解释结果

先检查`contract_version=alphalake-company-valuation-v1`、证券代码、报告期和截止是否匹配请求，再读取顶层`status`、`selection`和`valuation`；不要从`candidates`挑一个成功值冒充默认结果。

| 结果 | 处理 |
| --- | --- |
| 退出0、选中估值成功 | 报告所选模型、每股值及币种、财务/信息时点和股本依据；WACC是小数比例，展示时转百分比。仅经营价值模型可能无每股值，保持为空。 |
| 退出2、业务阻断 | 列出具体身份/来源/缺项/政策问题。专项缺项或过期时即使行业候选成功也不降级；可以单列其候选值和不同口径。 |
| `blocked_ambiguous_policy` | 说明同级候选及差别；缺少选择依据时请用户选择，不通过文件顺序或最高价格裁决。 |
| 退出1、运行失败 | 读取`reason`、`error_type`及stderr定位配置/导出/环境问题；不要把失败当成估值0，也不无限重试。CLI语法错误可能只有argparse文本。 |

必要时读取`valuation.evidence.run_file`或候选对应运行文件中的`request`、`audit`和`report`，按`run_id`追溯。先读摘要，按问题展开证据，避免把整个财务快照或全部历史运行塞入上下文。文件缺失如实报告，不根据文档数字伪造运行。

回答通常给一张简短结果表，加关键假设/阻断和运行引用。不得把条件估值称为当前目标价；`share_date=null`不等于股本日期就是财报期。区分主库与审核隔离库：另一库拥有专项附注，不证明当前库已补齐。

## 发现历史运行

用户未提供run ID时，先读`docs/valuation-run-query.md`，从已确认的运行目录查询；不要求用户查找哈希，不为了获得ID重新计算。

```bash
cd "$ALPHALAKE_ROOT/valuation/backend"
"$ALPHALAKE_PYTHON" -m tools.list_valuation_runs "$ALPHALAKE_CODE" \
  --run-dir "$ALPHALAKE_RUN_DIR" --period "$ALPHALAKE_PERIOD" \
  --latest-per-model > "$ALPHALAKE_HISTORY"
```

目录和模型可用重复的`--run-dir`、`--model`限定；`--as-of`是带时区的信息截止上限。仅使用已确认目录，不递归搜索整个workspace或静默切到另一个库的结果。

检查`contract_version=alphalake-run-query-v1`、查询条件及`status`。退出2/`partial`或`latest_selection_complete=false`时先报告具体不完整原因，不断言哪个最新；`no_matches`只代表配置范围内无匹配。退出1报告请求/环境问题。

`has_more=true`时按`offset/limit`继续取页，取得所需候选及元数据。只有模型组的`unique_latest_run_id`非空且证券、模型、期间、情景、资本口径符合用户意图，才可直接选取；从对应`results[].locations[].path`取得比较目录。最新按`information_as_of`，不是计算创建时间；同截止并列时展示模型、情景、WACC、资本口径及证据差异，请用户明确业务口径，不按文件时间、ID顺序或估值高低裁决。即使价格相同，不同run ID也不是重复记录。

查询只验证保存请求与引擎哈希，摘要金额尚未重算；需要核验两端报告和解释差额时再调用比较入口。记录扫描不是原子快照，运行目录有并发发布时重新查询。

## 比较已有估值

需要比较两个已知运行时，先读仓库的`docs/valuation-comparison.md`，使用已有run ID及其实际目录：

```bash
cd "$ALPHALAKE_ROOT/valuation/backend"
"$ALPHALAKE_PYTHON" -m tools.compare_valuations "$ALPHALAKE_BEFORE_RUN" "$ALPHALAKE_AFTER_RUN" \
  --run-dir "$ALPHALAKE_RUN_DIR" > "$ALPHALAKE_COMPARISON"
```

另一端不在同目录时提供`--after-run-dir`。命令从保存请求在当前引擎重算核验，但不访问数据库、不写新估值。未找到运行或不能复现历史报告时报告拒绝，不照文档数字拼接结果。

检查`alphalake-valuation-comparison-v1`及`attribution.status`。只有`verified_wacc_only`可引用其WACC贡献；`not_attributed`只说明变化因素与总差额，不能全部归于WACC。同时列明双方模型、财务/截止/股本口径、标准ID差异和规范化变化；差异被截断时按JSON Pointer到原文件查明，不把差异条数当作金额变化条数。

若仅解释一个已有运行，直接读取其已保存证据并注明原时点，不必启动一次新计算，也不能声称旧运行代表最新数据。
