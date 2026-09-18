# 长期资产处置现金的标准链（2026-09-18）

本轮补足净资本开支的一项通用输入：TDX **FN110** 为处置固定资产、无形资产和其他长期资产收回的现金净额。它与FN301处置损益、FN108收回金融投资、FN111处置子公司现金分别独立；不能互相替代。[TDX官方目录](https://help.tdx.com.cn/quant/docs/markdown/TdxQuant.md/mindoc-1h10m001ic888.html)提供字段定义，已有安克三期合并现金流量表和原始gpcw包核对金额、元单位及年初累计口径。

这一分量用于判断购建支出扣除长期资产处置回款后的现金需求，不能直接批准公司资本倍率。并购、非现金投入、研发/租赁、折旧以及完整营运资本仍需分别协调。本轮不更改FCFF或再投资定义，不计算新的公司倍率。

| 安克报告期 | 合并现金流量表，元 | PDF页码 | TDX源位 | 标准物化 |
| --- | ---: | ---: | ---: | --- |
| 2025H1 | —（台账记0） | 74 | 0 | 零值歧义，拒绝 |
| 2025全年 | —（台账记0） | 115 | 0 | 零值歧义，拒绝 |
| 2026H1 | 17,350.00 | 64 | 1183288320 | 17,350元，H1累计 |

两个破折号已在原文定位，但本轮不将一家公司原文审核升级为全市场“TDX零均为真实零”规则，也不把破折号回填成标准事实。本期累计处置现金已接通，**TTM标准覆盖仍为1/3，标准TTM保持空值**；后续已完成下述两期显式零值审核，另层组成3/3现金分量，不放宽源解析或改写标准窗口。

## 实现与验收

- 迁移045增加FN110累计元映射，代码目录为81字段；物化增加FN110零值歧义拒绝。适用起点2025-01-01；未宣称已审核所有公司的原文。
- 真正经过归档源包→解析→物化→时点查询→估值导出；新测试复用原真实双公司底座，随后单独应用045。原历史计数测试固定原字段范围，新增FN110由独立测试验收。
- 安克三期源位和标准金额、日期边界、非法倍率撤销既有事实、恢复、幂等及关闭重开通过；[隔离导出](standard-export.json.gz)与[范围回执](receipt.json)可复验。
- 统一估值的`company_capital_evidence`在存在FN110窗口时增加处置现金、扣处置购建现金及完成状态。缺任一标准分量返回null；原本无该窗口的旧快照保持原诊断形状。资金投入诊断不回写财务事实，不修改经营预测或股权桥接。
- 原文校验重新提取三个本期行，验证PDF哈希、合并表章节、项目及本期列；三路金额加0.01元均拒绝。伪造完整TTM窗口亦由共享血缘校验拒绝。Python回归验证增加诊断前后的完整DCF报告相同。

标准导出来自`workspace/disposal-cash-20260918/acceptance.duckdb`，不是正式主库。**生产主库仍是44、80字段；本轮未迁移或物化主库，不把代码81字段冒充全市场交付。** 已发布的两家公司资产政策、原始库及既有运行不变。未来主库升级需按既有约定另做副本物化、差异验收与备份发布。

## 两期零值审核及混合来源分量

[零值审核清单](zero-supplements.json)将2025H1、2025全年两项破折号明确解释为该合并现金流量表本期零：逐项核对收回投资现金、取得投资收益现金及其他投资流入，与本期投资流入小计闭合；同时核对FN110源零位。表内项目与期间语义是审核依据，哈希与加总并非独立语义证据链。没有把“某行未列示”自动判为零。

两条补充使用既有`import-supplements`写入隔离库；各自绑定公告、PDF页码/哈希、累计期间、元单位、审核人与2026-09-18T13:30:43Z审核时间。首次2条、重放0条，重开导出；底层TDX零位不变，也没有新增两条标准零事实。

`nonfinancial-reviewed-history-fcff-v1`增加可选`disposal_cash_zeros`，每项明确期间、补充导入哈希、导出内容哈希及TDX源包哈希，沿用公司、报告期及审核有效期约束。用同期间标准FN114核对公告和源包身份，仅对FN110窗口明确缺失的期间采用，拒绝覆盖已有FN110事实；重复期间、错单位/期间/原文/源包、非零补充、失效或撤销均拒绝。该入口目前附属于已有审核资产政策，不是全市场零值自动解释器。

| 2026H1截止TTM分量 | 元 | 来源 |
| --- | ---: | --- |
| 购建长期资产现金 | 319,634,280 | 标准FN114，保留float32精度 |
| 处置长期资产现金 | 17,350 | 本期标准FN110 + 两期显式审核零 |
| 购建支出扣处置回款 | **319,616,930** | 上两项相减 |

输出继续保留`asset_disposal_cash_million_cny=null`及标准缺期状态，另列`reviewed_asset_disposal_cash_million_cny=0.01735`、`reviewed_cash_capex_after_disposals_million_cny=319.61693`和逐期混合血缘，避免将补充结果标成标准TTM。未扣折旧，未含全部并购、研发、租赁及营运资本，**不是完整净再投资或历史FCFF**。

[零值验收回执](zero-acceptance.json)记录真实导入、统一入口及增量重估。独立临时链撤销2025H1审核：旧发布重放不复活，采用政策拒绝；恢复生成新审核头，旧政策仍拒绝；显式重新绑定后恢复。标准事实和窗口完全不变，DCF完整报告不变，固定原经营假设/WACC下安克仍为127.1544634851元/股，不是当前市场目标价。证据改变触发新运行，同输入复用。

标准映射及两条零值补充均只在隔离链验收，**正式主库仍44，本轮没有生产发布**。两个标准缺期仍保留；这次闭合的是显式审核后的现金分量，不是公司资本倍率批准。

## 复验

```bash
ALPHALAKE_DISPOSAL_EXPORT_DIR=/tmp/alphalake-disposal-check \
 go test ./internal/ingest -run '^TestRealAssetDisposalCash$' -count=1
PYTHONPATH=valuation/backend python -m tools.verify_asset_disposal_cash
PYTHONPATH=valuation/backend python -m pytest -q valuation/backend/tests/test_asset_disposal_cash.py
```

Python使用已有backend环境及pypdf；新增测试随CI现有backend pytest运行。Go真实源链不依赖Python或外部下载；Python原文核验不能被Go通过代替。没有新依赖或新PDF副本。

本轮最终验证：Go全套、入口构建及相关vet通过；Python相关96项通过、无skip（2条既有依赖弃用warning），最后追加完整窗口减法方向的受控断言另单项通过。正式主库未写入。

零值生命周期及采用复验（已有backend环境）：

```bash
ALPHALAKE_DISPOSAL_EXPORT_DIR=workspace/disposal-zero-recheck \
 go test ./internal/ingest -run '^TestRealAssetDisposalCash$' -count=1
PYTHONPATH=valuation/backend python -m tools.verify_disposal_review workspace/disposal-zero-recheck
PYTHONPATH=valuation/backend \
ALPHALAKE_VALUATION_RUN_DIR=workspace/disposal-zero-recheck/runs \
python -m tools.company_valuation workspace/disposal-zero-recheck/acceptance.duckdb 300866 \
 --period 2026-06-30 --as-of 2026-09-18T13:30:43Z \
 --policy workspace/disposal-zero-recheck/zero-policy.json --alphalake ./alphalake
```

工具生成的政策绑定隔离链最后的恢复版本；不能复制到不同源包或不同审核头的数据库。该测试在自己的临时库保留两条首次发布和一组撤销/恢复；不在正式库制造测试审核历史。Python回归直接调用真实Go链，再校验原文、采用、失效、恢复及11路负向输入，已随现有backend pytest进入CI。

零值闭合这一轮：Go全套与入口构建通过，Python相关97项通过、无skip（2条既有依赖弃用warning）；真实统一CLI运行ID与恢复后重绑定运行一致。
