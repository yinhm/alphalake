# TDX 财务全量源目录与解析

> 本文记录schema47目录首发验收。当前已按[ADR020](decisions/020-official-statements-and-snapshots.md)提供281项标准映射/schema50及三表快照，命名依据为官方461、参考1；以下81、437、25等数字是首发时点，不是当前总数。

截至2026-09-19，本轮把按需求追加的源适配改为完整目录驱动。当前真实包584个位置全部保存并可查询，新增已定义字段不需要再写解析分支。这里的“完整”指源位置覆盖，不代表所有字段已获标准事实审核或可直接进入估值。

## 范围与证据

| 范围 | 数量 | 当前处理 |
| --- | ---: | --- |
| 真实包位置 | 584 | 全部保留float32位及来源；解析器仍按包长度读取，不截断未来位置 |
| 官方有意义的字段定义 | 437 | 通达信官方专业财务说明；原中文描述逐项对照冻结HTML |
| 仅参考词典补充定义 | 25 | portfolio的74–97、165；明确标为reference |
| 有名称依据合计 | 462 | 使用通用业务名称，保留定义来源与审核状态 |
| 未公布语义 | 122 | 不编名称、单位或期间，保留源证据 |
| 有明确数值单位尺度 | 422 | 可做源单位换算；不是422项均可发布标准事实 |
| 比例尺度尚不明确 | 37 | 不猜百分数/倍数，规范值为空 |
| 日期编码 | 3 | 显式YYMMDD原编码，不虚构世纪、时区、精确公告时间 |
| 已审核标准映射 | 81 | 本轮数量、适用期间及既有财务数值不变 |

单位明确的422项还包含两个同名贷款位置，需先消歧；零值及非有限值也有逐值阻断。因此“有单位”不是有效观察数量。未审核的381项已命名字段不是标准财务完成度的新增分子。

主要依据是[通达信官方字段表](https://help.tdx.com.cn/quant/docs/markdown/TdxQuant.md/mindoc-1h10m001ic888.html)及[专业财务单位与缺失规则](https://help.tdx.com.cn/gspt/docs/markdown/redword/Profinance)。后者说明未特别标注时金额为元、股数为股，缺失数据展示为零。官方表438行含一个“暂无”，不能算438项有效定义。

`~/portfolio/datafeed/types.py`仅作为参考，原作者credit为 **Copyright 2021 yinhm**，冻结于提交`a16e6fc`；沿用[ADR014](decisions/014-open-source-fn-balance-profit.md)的证据边界，不因旧英文键或float64声明就认定业务语义或源精度。该文件在此不执行、不引入numpy，也不沿用截断580位置的实现。

三份原始文件均压缩冻结在[source testdata](../internal/source/tdx/financial/testdata/)，[收据](../internal/source/tdx/financial/testdata/official-financial-fields.receipt.json)保存URL、取得日期、字节数和SHA256。网页发布日期未知，不借用取得日期。逐字节哈希验证的是归档完整性，不是独立会计核验。

## 数据链与使用

唯一完整源目录是[`catalog.csv`](../internal/source/tdx/financial/catalog.csv)，Go内嵌，研究适配器直接读取同一文件。schema47新增`fundamental.source_field`：它描述供应商位置，不代替`fundamental.field`标准语义或`fundamental.provider_field`审核映射。

原包 → `provider_fact`全部源位 → 源目录解释/换算 → 已审核映射及公告关联 → 标准事实 → 派生/估值。源查询不会物化标准事实，PDF不覆盖TDX。

```bash
# 源维护：全目录，包括未定义项和审核状态
go run ./cmd/alphalake tdx-financial-fields

# 源维护：本地该公司该报告期的所有源修订，按通用名称输出
go run ./cmd/alphalake export-financial-source \
  workspace/auto-valuation-20260909/market.duckdb 300866 --period 2026-06-30
```

第二个命令是`alphalake-source-financial-v1`源观察契约，明确标注`all_local_source_revisions_not_standard_facts_or_pit`；保留全部修订，不隐式挑最新，不声称PIT。业务名在`field`，编号、原始float32位和参考依据在`source_evidence`，另带原事实、修订及归档ID；缺编码不伪造零位。未知位置没有业务名。

既有`export-valuation`及统一估值入口继续消费标准事实，不能以该源维护出口绕过审核。研究适配按通用名称自动支持目录内可解释的报表金额/股数，预告、快报、供应商TTM/比例、未知期间及同名歧义不能当普通累计量聚合。

## 已明确的边界

- 未公布不等于无数据：安克2026H1源位置363实际有非零编码值3523，但未找到可信字段定义，原位保留，不据数值猜业务含义。
- 官方两个金融贷款位置描述完全相同，标为`ambiguous_source_variant`；不随意选一个，也不把两者相加。
- 每股收益增长率是百分数，不是元/股；股东持股数量是股数，不是机构家数。元/万元、股/万股、比例、人数及日期分别解释；比例保持明确的percent单位，不静默改为小数。
- 供应商计算指标以`reported_*`命名。供应商自由现金流、EBIT、ROIC与TTM不冒充按达摩达兰方法从标准事实推导的结果。
- 源零除明示审计代码外标`zero_requires_review`；不以完整字典证明缺失为零。标准物化改用目录`zero_policy`，仅41项既有允许零的映射显式allow，40项既有拒绝项继续reject，新增映射默认reject。
- 81项审核映射与完整源目录的名称、单位、倍率、期间由回归锁定。后续标准准入仍要补足真实报表范围/期间证据、有效期和零值政策，但不再为该字段开发源解析器。

## 验收

Go回归使用真实裁剪包逐记录核对584个位置的位保留及换算，官方438行描述与冻结HTML全量对照；错误位值、非有限值、未知未来位置、零值、同名歧义、单位误分类有拒绝断言。真实标准链重开后同时读取584项源观察，既有标准事实验收保留。Python研究适配新增字段直接取数与错误类别拒绝均进入现有pytest流程。

本地库接入、逐表摘要、估值输入比较及备份轮转结果见本轮[接入收据](acceptance/tdx-financial-catalog-20260919.json)。本轮不新增公司原文金额核验，不声称全462项完成逐公司财报审核，也不改变达摩达兰估值公式。

验证结果：`go test ./...`、CLI构建、`go vet ./...`通过；Python 3.12全套448通过、4项既有外部样本缺失跳过、7项既有警告。最终标签尺度修正后补跑目录与适配回归；原42个研究字段的编号、期间和倍率逐项不变。新增Go/Python回归进入既有CI，源目录不增加解释器或库依赖。
