# ADR 002——TDX GBBQ 快照与仿射复权区间

状态：已接受

## 背景

AlphaLake 需要公司行动、股本历史及可复现的前/后复权变换，同时不将复权 OHLC 作为主要事实存储。

TDX GBBQ 包含多种语义不同的事件类别。部分已验证会影响价格复权，其他类别仅描述股本事件。上游便利库也可能将派生因子缓存在私有数据库，重复 AlphaLake 自身的持久化/流程层。

## 决策

### 1. 按标准证券抓取 GBBQ，不调用黑盒全市场辅助函数

AlphaLake 刷新自己的证券主数据，再对每个符合条件的代码调用 TDX SDK。这样可以将故障归属到具体代码，支持部分完成和续传。

初始品种为股票与 ETF。

### 2. 逐证券原子保存完整源快照

某代码的 GBBQ 历史成功抓取后，AlphaLake 在一个事务内替换该证券/来源的公司行动与股本快照。

原因：

- TDX 可能修正或删除历史事件；
- 仅追加或更新会遗留已删除/过时事实；
- 抓取失败不得清除上次可信快照。

### 3. 保留原始 GBBQ 身份与字段

每条公司行动保留：

- TDX 类别；
- 原始 C1–C4；
- 稳定的 `source_record_id`；
- `ingest_run_id`。

标准语义是额外解释，不替换原始证据。

### 4. 只有验证过的类别生成股本事实

类别 2、3、5、7、8、9、10 被视为包含变动前后流通/总股数，可生成 `market.share_capital` 行。

其他类别仅保留为公司行动，除非另外验证其股数含义。

### 5. 价格复权只使用已验证类别

初始复权方法为 `affine_gbbq_v1`，使用：

- 类别 1 / 标准 `distribution`；
- 类别 11 / 标准 `scale`，即 ETF/LOF 份额折算。

类别 12 保留为原始/标准公司行动数据，但其对交易价格的影响尚未达到同等验证程度，因此 v1 不将其用于价格。

### 6. 使用仿射变换，而非仅用标量因子

分派事件：

```text
after = (before - c) / m
m = (10 + bonus_per_10 + rights_per_10) / 10
c = (cash_dividend_per_10 - rights_per_10 * rights_price) / 10
```

类别 11 的 ETF 折算：

```text
after = before / scale
```

AlphaLake 存储区间系数：

```text
adjusted_price = mul * raw_price + add
```

这精确保留现金分红的加法影响，纯标量因子一般做不到。

### 7. 复权事件映射到后续首个交易日

若 GBBQ 事件日不在日线 OHLC 中，例如周末、节假日或停牌缺口，则从已存储的、不早于事件日的首个交易日起生效。

这样可防止停牌区间内事件丢失。

### 8. 前/后复权是派生区间，不是主要 OHLC

前复权以最新可用交易日所在区间的 `(mul=1, add=0)` 为锚点，跨事件向前回溯。

后复权归一化后，最早可用区间为 `(mul=1, add=0)`。

`market.adjustment_segment` 只保存系数变化边界；每日复权价格按需计算，或在后续派生视图中生成。

历史区间的 `effective_to` 包含终点。最新区间为 `effective_to = NULL`。

### 9. 派生复权快照可重建并关联运行

`calc-adjustments` 只读取标准 DuckDB 数据：

```text
market.ohlcv_daily
+
market.corporate_action
→
market.adjustment_segment
```

它不访问网络。每次重建都有 `meta.ingest_run`，复权区间保存 `ingest_run_id`。

## 影响

- 原始 OHLC 始终是稳定的价格事实来源。
- 公司行动修正可触发确定性复权重建。
- ETF 类别 11 事件不会丢失。
- 不支持或不确定的类别保持可检查，不悄然影响价格。
- 算法可使用新的 `method` 值演进，不覆盖既有语义。
