# 原生估值的折旧与非现金营运资本口径

## 能力与边界

已增加`tools.derive_tdx_capital`：从权威DuckDB一次批量导出所需标准列，按公司、报告期计算候选和缺项，输出可追溯JSON。沿用已审核标准映射、原源精度及披露截止；不新增事实、不改SQLite准入、原页面、预测政策或DCF公式。提供通用核算与拒绝边界，尚未批准自动填入`d_a`和`change_in_noncash_wc`。

## 方法与计算契约

达摩达兰[非现金营运资本说明](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/noncashwc.htm)要求从流动资产剔除现金及有价证券，从流动负债剔除有息债务（含一年内到期部分）；负值不是解析错误。[现金流定义](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/CFTheory/deriv/ch14der.html)中的FCFF另需一致的经营收益、税、资本支出及营运资本变动。

| 输出 | 计算 | 不能据此认定的内容 |
|---|---|---|
| 来源隐含折旧摊销 | `reported_ebitda - reported_ebit` | 未审核租赁与其他调整范围，不自动等于经营D&A |
| 核心折旧摊销分量小计 | `depreciation_depletion + intangible_amortization + deferred_expense_amortization` | 合并列示范围可能变化，不固定追加使用权或投资性房地产折旧 |
| 现金流营运调节贡献 | 存货减少＋经营应收减少＋经营应付增加 | 不能反号后填成分类后的估值营运资本变动 |
| 会计营运资本 | 流动资产－流动负债 | 尚未排除现金、投资和有息债务 |
| 贸易营运资本小计 | 应收账款＋存货－应付账款 | 缺预付款、合同项、其他经营往来等，不等于完整非现金营运资本 |
| 非现金营运资本候选 | 流动资产－货币资金－交易性金融资产－流动负债＋短期借款＋一年内到期非流动负债 | 现金/投资混合范围、有息债务与非债务拆分未闭合，只作为待审候选 |

最后一行是根据现有标准项提出的**待审计算**，不是声称达摩达兰认可这些会计科目全部按该方式分类。货币资金可能包含受限款项；交易性金融资产不一定覆盖全部流动金融投资；到期非流动负债并非全是有息债务，应付票据、其他往来、衍生工具及财务子公司又可能需要调整。上下两期范围确认前，不输出候选差额冒充历史营运资本变动。

同一公式要求证券、单位、累计/存量期间、报表范围及源记录一致；缺项、冲突、混合源记录均返回空值和具体原因。缺项只表述为`missing_standard_fact`，不据此断言上游缺失。完整算术也标为`arithmetic_complete_not_valuation_approved`。每个原值保留独立来源证据，计算引用字段，不将残差归零或修复源float32精度。

已有范围反例见[核心财务语义](../decisions/009-core-financial-fields.md)、[租赁折旧](../decisions/010-tax-and-debt-fields.md)、[现金流调节项](../decisions/013-cashflow-research-fields.md)及[来源EBIT/EBITDA](../history/tdx-supplementary-fields-20260925.md)。哈希、减法一致性不代替独立范围核验。

## 使用与下一步决策

[预测依赖复验](../history/native-policy-economic-review.md#预测再投资与历史核验的依赖2026-09-28)已确认：当前DCF按销售资本比预测总再投资，不直接消费这里仍未审核的历史折旧及营运资本变动。以下两类问题约束历史FCFF及公司资本效率核验，不作为当前预测DCF新增准入门槛；主线先审核实际使用的资本倍率、增长与利润率政策。

在已核验资源硬限额的隔离服务内执行：

```bash
PYTHONPATH=valuation/backend .venv/bin/python -m tools.derive_tdx_capital \
  --database workspace/alphalake.duckdb \
  --output workspace/derived/capital-definition-review/five.json \
  --codes 300866,600519,002032,600690,600031 \
  --period 2024-12-31 --period 2025-06-30 --period 2025-12-31 --period 2026-06-30 \
  --as-of 2026-09-27T16:55:01+00:00
```

输出路径必须不存在；程序只启动一次批量财务导出，临时中间文件自动清理。没有事实或身份未解析的请求位置不会从分母消失。CI已有Python全套入口，会执行新增回归。

接入历史FCFF之前须解决两类不同问题：

1. **数据供给**：复用历史映射审核及公告关联通道，六项余额历史已补齐，后续按源零、披露关联等具体原因补链；不能通过扩大预测窗口或补零解决。
2. **经济分类**：用已有真实证据审核折旧包含范围、现金/流动金融资产及到期债务边界，再判断能否制定通用规则。TDX无法分离的混合项，如要采用来源隐含折旧或上述余额式代理，须另获明确批准、标注代理范围，并与EBIT、租赁、资本支出一致；本轮没有默认启用。继续以TDX为数值主源，CNINFO仅核验，不能改为逐公司PDF主元采集。

即使这两项通过，历史FCFF还须核对经营EBIT/税率、并购及非现金资本投入、研发资本化等口径。不能仅凭`d_a`或营运资本非空宣称完整FCFF已闭合。

历史样本数值、运行标识及测试收据见[验收记录](../history/native-capital-definition.md)；当前交付数量统一见[项目状态](../implementation-status.md)。
