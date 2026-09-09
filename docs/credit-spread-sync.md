# 合成评级利差与借款成本桥接

在既有参考发布链上接入达摩达兰 2026 年 1 月大型非金融企业表，新增迁移 028 的 `reference.credit_spread_band`。原始归档→严格解析→完整 15 档校验→版本／观测／检查点原子发布→显式版本导出→标准财务计算保障倍数→借款成本→共享 WACC／估值。未新增依赖。

## 来源与口径

来源：[Aswath Damodaran / NYU Stern Ratings, Interest Coverage Ratios and Default Spread](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html)。感谢作者提供参考数据。页面说明评级关系来自美国评级公司，利差来自交易债券；不等于中国企业债券报价或正式评级。只接左侧大型非金融表，不混入右侧金融公司表，也不替换 valuation 原有其他评级分支。

文件只声明 January 2026，使用 `observation_date=2026-01-01` 作月份载体，`observation_precision=month` 明确精度；陈旧天数从该月首日保守计算。`available_at` 为本系统首次取得时间，不能以月份标签倒推公开日。

保留原上下界文本、原百分数、评级名称、表／行／列定位，标准利差除以 100 保存 12 位小数。15 档上下界与名称均锁定本次审核范围；未来年份或变更区间先拒绝，核验后升级契约。来源标题是下界 `>`、上界 `≤`，原表有小缝隙，采用 `lower < coverage <= upper`：例如 0.199999 属第一档，0.2 及二者之间不匹配，禁止填缝或夹到最近档。超出 -100000／100000 的范围也拒绝。

来源归档 SHA-256：`71d5dba40bd0659b7d6fe6f0e2d25036442d66b3c5e309480d0c9b48b2c3ba63`。解析器是 Python 标准库 HTMLParser，按原页面声明的 Macintosh 编码读取；解析代码哈希和 Python 运行版本进入发布签名。

## 操作

```bash
go build ./cmd/alphalake
./alphalake sync-credit-spreads workspace/wacc/alphalake.duckdb --python python3
./alphalake sync-credit-spreads workspace/wacc/alphalake.duckdb --python python3 --offline

./alphalake export-wacc-references workspace/wacc/alphalake.duckdb \
  --as-of 2026-09-09T13:00:00Z \
  --country-release 3 --beta-release 1 --yield-release 2 --credit-release 4 \
  > workspace/wacc/references.json
```

替换实际库、发布编号和信息截止时间；四类参考数据必须位于同一库。离线模式零 HTTP，重放核对全部已发布列，坏文件保留失败运行与诊断而不推进成功检查点。`--credit-release` 省略时仍导出旧 v1 契约；提供时导出 v2（四个版本、15 档附加观测），两者接口均支持，原有三版本调用不变。

安克调用沿用[桥接 CLI](wacc-valuation-bridge.md)，将 `--wacc-policy` 换为 `valuation/examples/wacc/anker-synthetic-debt-policy.json`，参考文件换成本次 v2 导出。经营预测示例仍是旧 central 政策，仅供桥接验收。

新 `synthetic_debt` 政策与 `debt_cost_pretax` 二选一，必须说明大型非金融表适用理由、保障倍数口径、是否加中国违约利差及其理由、最大陈旧天数。首批保障倍数使用同一 TTM 的已审核政策营业 EBIT／TDX FN305 毛利息费用；分子沿用已有经营调整，分母不是净财务费用，不从利息／债务账面额反推当前借款成本。

```text
coverage = 已有政策营业 EBIT / TDX TTM FN305
Kd = 选定 RF + 匹配公司违约利差 + 显式选择的中国主权违约利差（或 0）
```

利息必须大于零；缺失、口径不匹配、区间缺口／范围外、不完整表、时点未来、陈旧、重复匹配均拒绝，不回退手填值。国家股权风险 CRP 不代替债务违约利差。选择“国债减中国利差”的 RF 再在 Kd 加回同一利差时，两项抵消是显式政策结果，审计仍分别保存。

茅台酒类代理 EBIT 与合并利息费用尚未证明同范围，首批明确拒绝其合成借款成本，保留原有显式 WACC／借款成本路径。也未推广至银行、金融公司或其他未审核证券。

## 验收与边界

2026-09-09 在 `workspace/wacc-beta-yield-20260909/acceptance.duckdb` 实际在线同步：run 6／artifact 4／release 4，15 档；离线 run 7 重放原版本，inserted=false。HTTP 估值使用既有真实财务库 `workspace/valuation-integration-20260907/exports/acceptance.duckdb`，输入与输出保存在 `workspace/credit-wacc-20260909/`。

安克本次 TTM EBIT 为 3230.457212 百万元，FN305 为 63.953754 百万元，保障倍数 50.512394，匹配表中 Aaa/AAA 档利差 0.40%。选择加入中国主权利差后 Kd=2.0816%，沿用 5% 目标债务权重等政策，WACC=6.935322%，每股模型输出 157.81 元。不是正式评级、当前借款报价或修订目标价；经营预测和资本结构仍含政策假设。

自动验收包括：真实归档经生产解析、在线重复、坏文件失败、离线零请求、重开库保留失败记录；15 个百分数逐项抄核与 Decimal 换算；完整版本导出及新旧契约；真实标准财务→HTTP→共享引擎及重复请求幂等；区间缝隙、零／负利息、陈旧表、冲突政策及茅台范围拒绝。哈希与固定样本不是独立市场校准。

仍待补：中国信用市场校准／债券报价、同日股权市值与债务市场价值、公司实际业务和地域风险暴露。合成借款成本只减少一项手填输入，不意味着市场化 WACC 全部闭合；历史 FCFF 仍留空。
