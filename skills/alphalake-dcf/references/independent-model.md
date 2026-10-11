# 独立 DCF 数学定义与算例

只共享原始数据和已明确的经济条件，计算代码、公式和结果核验独立。金额百万元报表币种，比例小数，股数百万股。这里是研究模型定义，不是生产实现的正确性声明。

## 利润、研发、税务及投入

FCFF为经济经营利润减现金经营税、再减同范围总净投入。明细投入：`I = 非研发资本开支 − 对应D&A + Δ非现金营运资本 + 其他明确经营净投入 + 研发净投入`。现金、有息债务不进非现金营运资本，投资及关联借款须分类；不将流动资产−流动负债直接认定为经营资本。

只对已费用化R建研发队列。采用“当期新增费用次年起直线摊销”的明示条件时，`A_t = Σ(R_{t−k}/n), k=1..n`；寿命n据经济依据选择，缺历史不补0，资产为各未摊销cohort之和。非年末TTM须另明确期间权重与队列基期，不作为额外一个完整年度。已资本化开发支出不重复加入。

设P为剔除明确非经营项目、仍含研发费用扣减的报表经营利润，E为经济EBIT，D为税务扣除，τ为适用税基税率：

```text
E = P + R − A
B = E + A − D                     # 简化无其他税会差异
U = min(L期初, max(B,0))           # 明确无到期／抵扣上限的条件
现金税 C = τ × max(B − U,0)
L期末 = L期初 − U + max(−B,0)
NOPAT = E − C
I总 = I非研发 + R − A
FCFF = NOPAT − I总
```

普通当期扣除D=R时B=P。会计税率仅代理；NOL金额、期限、税法限制及加计扣除资格未经核实不作事实，不用净利润亏损推税务亏损，不因E>0就认定纳税。经营税与债务税盾分开。[研发重分类原文](https://pages.stern.nyu.edu/adamodar/New_Home_Page/valquestions/R%26D.htm)、[税率与亏损原文](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/taxrate.htm)支持这种区分；具体队列时序和简化税制是本模型明示条件。

预测投入也可用 `I_t = 对应收入增量/增量销售资本倍率`，明确投资领先／滞后；或显式完整总预算，不与明细路径叠加。[增长与再投资原文](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/littlebook/growthvaluedrivers.htm)支持这些路径，行业／历史存量倍率不认证未来效率。负投入须资本回收依据，历史FCFF缺明细就留空，不借预测补历史。

## WACC

从AlphaLake原始参考重建，不读取被测 `cost_of_capital`。名义人民币现金流配同币种名义利率，参考日期／地域、公司暴露和信用代理留痕。

在零债务Beta、普通债务／权益且税盾条件一致的常用分支：

```text
βL = βU × [1 + (1−τ盾) × D市场/E市场]
Ke = Rf + βL × ERP成熟 + λ × CRP
Kd税后 = Kd税前 × (1−τ可用盾)
wE = E市场/(D市场+E市场)
wD = D市场/(D市场+E市场)
WACC = Ke×wE + Kd税后×wD
```

若采用含国家风险的总ERP分支，不再加CRP；λ和ERP分支不是公司统一事实。净现金不能产生负wD，现金放股权桥；市场债务缺失而用已批准账面代理需明示。历史利息／平均债务不是当下边际债务成本；优先股、转债须另定义权重。[资本成本原文](https://pages.stern.nyu.edu/~adamodar/pdfiles/papers/costofcapital.pdf)为方法依据，数值仍来自AlphaLake。

亏损时税盾是否／何时可用另审，不能直接套法定税率。完整未来融资路径未知只能明示条件；不从整体WACC反推债务分量，也不复制原生“调整后WACC”完成独立校验。

## 折现、稳态及过渡

年末：`DF_t = 1/Π(1+WACC_k), k=1..t`，`PV显式 = ΣFCFF_t×DF_t`。年中现金流时点另定；定义在年末N的终值仍按N折现，不机械沿FCFF最后的N−0.5时点。

稳态重新计算收入、E、现金税、NOPAT，再按[稳态增长与投入原文](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/termvalueexreturns.htm)：

```text
I正常化 = NOPAT正常化 × g/ROIC稳态
FCFF正常化 = NOPAT正常化 − I正常化
TV_N = FCFF正常化首年/(WACC稳态−g)
经营价值 = PV显式 + TV_N×DF_N
```

要求WACC>g、正ROIC、匹配币种／通胀和可持续终点；ROIC=WACC是无超额回报条件。负g不能自动证明无限资本回收，有限寿命须专项模型。

稳定研发队列为 `A首年稳态=(R首年/n)Σ(1+g)^(−k), k=1..n`；普通扣除时R仍未必等于A。保持报表利润率与保持经济利润率是两种终点，不能混用。旧队列、NOL及可用税盾未稳态时，独立逐年计价到确定恢复稳态后接解析永续值：

```text
TV_N = Σ_{j=1..K}(FCFF实际_j/Π_{k=1..j}(1+r_k))
       + TV_{N+K}/Π_{k=1..K}(1+r_k)
```

无法确定有限恢复期时解析计价或明确未实现，不固定截100年冒称精确无限期校验，也不抄原生 `terminal_tax_transition_value`。

## 股权与其他索偿

经营价值加同范围非经营现金／投资，减债务、少数权益、优先及其他索偿，普通股权益除现有股份。受限现金、账面／市场代理和经营收益范围分别留证，防止重复加价值。

转债可独立按合同现金流和可比纯债收益率估B，以含权市值M−B估权利O；撤销已扣账面额后扣B与O各一次、除现有股份，不再全转股摊薄。员工期权单独扣价值也不重复加同一组股份。[其他权益索偿原文](https://pages.stern.nyu.edu/adamodar/New_Home_Page/lectures/eqshare.htm)支持拆分。缺价格、合同或期权数据就列未比较范围，不按权利0或照抄被测值声称完整验证。

## 可执行基础算例

全部为虚构条件，**不是完整公司模型、默认参数或全模型验证**。新分支需增加独立反例。

```python
from decimal import Decimal as D

def operating_value(flows, rates, terminal_fcff, terminal_rate, g):
    if not flows or len(flows) != len(rates):
        raise ValueError("现金流与折现年度须完整")
    if any(not x.is_finite() for x in [*flows, *rates, terminal_fcff, terminal_rate, g]):
        raise ValueError("输入必须有限")
    if any(r <= -1 for r in rates) or g <= -1 or terminal_rate <= g:
        raise ValueError("折现无效或终值WACC不高于g")
    factor, pv = D(1), D(0)
    for flow, rate in zip(flows, rates):
        factor /= 1 + rate
        pv += flow * factor
    return pv + terminal_fcff/(terminal_rate-g)*factor

# 两年100，之后永久100，10%年末折现，价值1000。
v = operating_value([D(100), D(100)], [D(".1"), D(".1")], D(100), D(".1"), D(0))
assert abs(v-D(1000)) < D("1e-20")
try:
    operating_value([D(100)], [D(".1")], D(100), D(".02"), D(".02"))
except ValueError:
    pass
else:
    raise AssertionError("非法终值不得返回0或数值")

# 研发重分类本身不创造FCFF。
p, r, a, tax, nonresearch_i = map(D, (200, 100, 40, ".25", 50))
e = p + r - a
cash_tax = (e+a-r)*tax
fcff = e-cash_tax-(nonresearch_i+r-a)
assert (e, cash_tax, fcff) == (D(260), D(50), D(100))
assert fcff == p*(1-tax)-nonresearch_i

# 总债务权重，不因现金而出现负债务权重。
debt, equity, rf, erp, kd = map(D, (100, 900, ".02", ".06", ".04"))
beta = 1+(1-tax)*debt/equity
wacc = (rf+beta*erp)*equity/(debt+equity)+kd*(1-tax)*debt/(debt+equity)
assert abs(wacc-D(".0795")) < D("1e-20")
```

同源数据不提供独立来源证据；相同数学定义、不同实现是算术校验，不认证经营假设或预测有效性。全模型须逐层比较，定位首次差异。
