# 市场权益权重到 WACC

首批实现安克 300866（A/H 两类）和茅台 600519（A 类）的标准市场输入到估值 API。输出 `capital_structure_basis=market_equity_estimated_debt`：股价来自市场，股数沿用最新审核披露，债务和经营范围调整采用明确代理。**这是可运行、可复验的市场权重估计链，不是所有输入都已经取得市场公允价值。**

## 数据层与使用

迁移 030 建立 `ref.listing`、`ref.listing_identifier`、`market.share_count_observation`、`market.listing_close_observation`。沿用原 `ref.company/instrument` 与不可变归档、发布版本、检查点、运行及失败诊断。A 股继续使用原 TDX instrument ID，不重建平行财务证券；H 股是同公司下的另一证券。listing 起点仅为本次证据确认日期，不能解释成初始上市日期。仅开放已审核文档及类别；尚非全市场自动身份治理。

```bash
# 先取得 TDX 主数据与未复权日线。各命令均在仓库根执行。
./alphalake sync-daily market.duckdb sz300866
./alphalake sync-daily market.duckdb sh600519
./alphalake sync-share-classes market.duckdb 300866 --python /path/to/python
./alphalake sync-share-classes market.duckdb 600519 --python /path/to/python
./alphalake sync-hkex-close market.duckdb 2026-09-08
./alphalake sync-hkd-cny market.duckdb

# release ID 必须取各同步命令实际输出，不能照抄不同数据库的编号。
./alphalake export-market-capital market.duckdb 300866 \
  --date 2026-09-08 --as-of 2026-09-09T16:00:00Z \
  --share-release SHARE_ID --hk-release HK_ID --fx-release FX_ID > anker-market.json
./alphalake export-market-capital market.duckdb 600519 \
  --date 2026-09-08 --as-of 2026-09-09T16:00:00Z \
  --share-release SHARE_ID > moutai-market.json
```

股本解析需要 pypdf 和系统 `pdftotext`（Poppler）。三个新市场同步命令都支持 `--offline`，按来源 URL 核验本地归档；港股必须精确对应所请求日期。相同原文、解析器、运行时不增发版本；失败原文仍归档，但不发布可信观测。改解析器或运行时产生新解释版本，不覆盖旧版本。

`sync-share-classes` 当前明确固定为安克 2026-08 月报、茅台 2026H1 报告，不是自动寻找最新公告。下一份文档要复核版式与类别后扩展适配器；陈旧上限会阻止无限沿用。`sync-hkex-close` 可传日期，但当前只审核 00668 的报价行。不能把这两个适配器宣称为已支持所有发行人。

导出在同一读事务选定股本、身份、A/H 价格、汇率和发布血缘。A 股必须有升级后、交易日结束后采集的 TDX 观测；港股同样要求在当地次日零点后取得。未复权收盘价与同日中间价，不回退前一交易日；缺类别、缺 FX 或时间不满足均失败。股本为 issued/treasury/outstanding 三条独立观测，公司总股数不复制给 A/H 两类。当前不自动支持 ADR、优先股或同股多地报价的组合。

复用 `export-wacc-references` 的四个版本与 `valuation/examples/wacc/{anker,moutai}-market-policy.json`。原调用增加 `--market-capital`：

```bash
/path/to/python valuation/backend/tools/valuate_alphalake.py FINANCIAL_DB 300866 \
  --period 2026-06-30 --as-of 2026-09-09T16:00:00Z \
  --policy valuation/examples/wacc/anker-forecast-policy.json \
  --wacc-policy valuation/examples/wacc/anker-market-policy.json \
  --wacc-references references.json --market-capital anker-market.json
```

金融、参考与市场快照的 information cutoff 必须相同。参考版本可来自独立参考库；数据引用固定内容签名、原文 SHA、定位、版本和实际值。API 结构校验不等于数字签名，不能让不可信客户端自造 JSON 后声称由数据库认证。

## 计算与政策

1. 股票权益市值 `Σ(类别 outstanding × 类别未复权 close × FX)`，单位在入引擎时转换为百万元人民币。A 股 FX=1；H 股使用同日外管局人民币中间价，明确不是收盘即期汇率。
2. 与已有股权桥接一致反推经营权益：`E=(股票市值−非债务桥接项目之和)/经营所有权比例`。安克扣除超额现金/金融投资，并保留少数股权、转股选择权账面代理及其他索偿；茅台扣除所持财务公司权益及非经营资产池，反推全部酒业经营权益。这里是既有分析政策，不是新增标准市场事实。
3. D 来自同一标准财务/审核附注链：安克短长借款、到期借款/债券/租赁、债券负债成分及租赁负债；茅台仅已审核租赁口径，不计财务公司存款。当前明确使用账面代理，未获得所有债务逐笔现金流与市场价值；不把可转债全市场报价同时作为 D 和选择权重复计入。
4. `d=D/(D+E)`；`βL=βU×[1+(1−税盾率)D/E]`；`Ke=Rf+βL×成熟市场ERP+国家风险分量`；`WACC=(1−d)Ke+d×Kd×(1−税盾率)`。金额与权重必须相符，市场分支拒绝另填 target_debt_weight。
5. 安克 Kd 由已审核 EBIT/FN305 覆盖率选择合成评级利差；茅台酒业与合并利息范围不同，仍拒绝套此比率。茅台示例显式选择高等级利差情景，标记 `analyst_credit_band_proxy_not_observed_rating`，既非观测评级也非自动合成评级，改变评级情景会重新取参考利差。

行业、地域风险暴露、税盾、无风险调整方法及终值沿用当前 WACC 均需研究政策。示例采用单行业、中国风险暴露；安克的全球业务组合尚未精确归因，不将本轮数值称为最终投资判断。政策分别限制报价、股本、财务债务/非经营调整的陈旧天数；超限拒绝。

**本次只更新 WACC**。原 DCF 是六月末财务状态的条件模型，安克七月 H 股募集资金、期后现金/债务变化及当前稀释尚未完整滚动至该 DCF 股权桥接。新 WACC 不会自动把旧每股值变成当前目标价，也不消除历史 FCFF 缺项。

## 来源、版式与 credit

- 安克股份：[交易所披露月报](https://disc.static.szse.cn/disc/disk03/finalpage/2026-09-04/2db3ed6a-0a7e-4f37-82eb-a03ec2045ec8.PDF)，p3 A/H 的月底 issued（含库存）、库存和 outstanding；p1 注册总额核对类别合计。A=537,978,302，H=50,076,100，两类库存均为 0。该 PDF 缺 ToUnicode，pypdf 无法可靠还原，采用 Poppler 标准 Adobe 映射，记录工具版本；不手写乱码替换表。
- 茅台股份：[2026H1](https://static.cninfo.com.cn/finalpage/2026-08-15/1225475868.PDF)，p22 三行核对 A 股和总量，p23、p72 确认 2,188,614 股已全数回购注销，余 1,250,081,601 股。库存零依赖注销数量桥接和完成说明，不依赖空金额单元格。
- 港股报价：[HKEX 2026-09-08 日报](https://www.hkex.com.hk/eng/stat/smstat/dayquot/d260908e.htm)，`#quotations` 每证券两行：第一行是前收/卖价/高价/股数，第二行是收盘/买价/低价/成交额。00668 收盘为 HKD128.20；不能误取第一行卖价129.10。完整24,349,137字节响应 gzip 存入测试夹具，解压 SHA `950588d0e95593f244bbc8ca642ecb4491bfa6fb13a6915ad03b59dec0b3e7c1`。同步大小上限仅该来源提高至64MiB，其余参考源仍16MiB。
- [外管局汇率](fx-sync.md)、TDX 日线、[中债/Beta](beta-yield-sync.md)、[国家风险](country-risk-sync.md)、[信用利差](credit-spread-sync.md)。数据及方法 credit 分别归发行人、交易所、国家外汇管理局/中国外汇交易中心、中债及 Aswath Damodaran。继续保留 valuation 上游 credit，未改其独立直接 WACC 分支。

本轮全部财务源事实及80个 FN 映射不变，原文股数进入独立市场股本层，不覆盖 FN238 的 float32 精度。

## 实际验收（2026-09-09）

隔离市场库 `workspace/market-wacc-20260909/market.duckdb` 使用生产采集/发布/导出；财务读取既有 `workspace/valuation-integration-20260907/exports/acceptance.duckdb`，参考读取 `workspace/wacc-beta-yield-20260909/acceptance.duckdb`，原财务与参考库未改写。两次 TDX 日线同步分别写1467/6001行、隔离0、主数据分区失败0，均实际通过新换节点机制恢复后完成。市值日2026-09-08，运行实际截止见[固化验收摘要](../valuation/examples/wacc/market-acceptance-20260909.json)。

| 项目 | 安克 | 茅台酒业代理 |
| --- | ---: | ---: |
| A股未复权收盘，CNY | 127.89 | 1309.30 |
| H股未复权收盘，HKD | 128.20 | 不适用 |
| 同日 CNY/HKD 中间价 | 0.86482 | 不适用 |
| 股票权益市值，亿元CNY | 743.539784 | 16367.318402 |
| 调整后经营权益E，亿元CNY | 686.339388 | 15202.695520 |
| 有息债务代理D，亿元CNY | 30.669158 | 2.436849 |
| 债务权重 | 4.277377% | 0.016026% |
| 税前Kd | 2.0816% | 2.0816%（等级情景） |
| 条件WACC | 6.944929% | 5.037922% |

股本距行情日期分别8天、70天，债务及非经营财务基期距行情70天。行情是前一交易日收盘，中债是信息截止时已经取得的9月9日曲线；不声称三者在同一秒成交。旧目标权重政策保持可选；未修改经营增长等假设来凑WACC。

真实HTTP请求重放结果逐字节一致，按内容留存运行文件。独立 `python3 valuation/backend/tools/verify_market_wacc.py 保存的API运行.json ...` 不调用引擎，复算市值、股权桥接反推、D/E、Beta、Kd和WACC，金额/比率在浮点界限内一致；它验证代数与单位，不是另一套财务取数证据链。市场WACC不能消除桥接项目使用账面值的估计误差。

源/存储测试覆盖原文版式、股数恒等式、港股第二行收盘列、换算、坏响应、重放、失败记录重开、缺类别/日期及ASOF拒绝。`TestMarketCapitalArchiveReplay` 已入CI（A股价格使用显式构造值测试关联，真实TDX验收另如上）；后端调用该生产导出和既有真实标准财务/参考导出，再实际请求API，独立Decimal复算并执行16路缺项/篡改拒绝。历史FCFF保持NULL。

全套Go测试、构建、vet、diff检查通过；后端195项通过、4项既有外部测试跳过。新增系统工具Poppler，记录解析运行时版本；Go及Python包依赖版本未变。

## 合同债务补证

新增安克可选 `anker-contractual-debt-policy.json`：导入24个已核验的合同期限表附注后，按参考Kd计算现值区间，显式选择上界用于WACC。茅台账面近似政策补充了半年报第91页的原文依据。完整计算、实际结果及仍未交付的期后融资事件链见[剩余缺口复核](wacc-gap-review-20260909.md)。API明确输出 `current_fair_value_complete=false`，不能因市场权重可算就宣称全部当前公允价值已闭合。
