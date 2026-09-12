# 普通非金融企业估值闭环验收

本轮完成的是**现有通用条件FCFF的计算、输入衔接及边界验收**，不是公司全部经济输入闭合、当前目标价或预测准确性认证。固定安克300866、苏泊尔002032；不扩大公司数、不新增增长候选、不等待2027年实际值。方法与实现逐项对照，缺口明确保留，不以“存在M1—M6模块”代替实际接入验收。

## 方法与实际接入

实际链路：`tools.company_valuation` → 只读标准就绪度／`export-valuation` → `build_inputs`／`build_historical_dcf_inputs`／`build_book_dcf_inputs` → `run_full_valuation` → M1—M6 → 原生股权桥接与不可变运行。历史FCFF没有被预测FCFF覆盖。

| 环节 | 达摩达兰依据与适用条件 | 当前通用路径 | 本轮结论／未闭合项 |
| --- | --- | --- | --- |
| 财务来源 | 经营收益与现金流的期间、单位及范围一致 | TDX标准事实／时点窗口，CNINFO提供身份与公告血缘；资金百万人民币、股数百万股 | 已接入；源精度保留，附注不静默替换源值；不扩大逐公司原文审核范围 |
| 经营EBIT | 剔除融资及非经营收益，保留经营活动的真实收益 | FN86+FN305−FN306−FN83−FN82−FN301 | 已接入调整代理；经营套期未恢复，租赁利息是否包含仍有公司口径缺口 |
| 研发／租赁 | [无形资产重分类](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/littlebook/intangiblevaluedriver.htm)须同时调整收益与资本／再投资 | 共享M1有相关机制；本路径研发保留费用化，未启用额外租赁重分类；FN439已入债务桥接 | **机制存在不等于已接入**；不盲目再资本化已入账租赁，也不把研发支出增长直接当收入增长 |
| 税项／NOPAT | 经营税与债务税盾分开，预测税率需有政策依据 | 显式逐年税率；两个样本均25%；WACC税盾另有25%政策 | 算术闭合，实际税盾可利用性／优惠到期未自动论证；不是公司实际有效税率 |
| 增长与再投资 | [增长依赖投入及回报](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/invfables/growthdeterminants.htm)，不能只给增长不计资本需求 | 历史季度同比中位数＋上限／偏移；Δ收入／收入资本比计算净再投资 | 同一收入路径已传入利润与再投资；行业平均为显式边际代理，研发／租赁口径与公司尚未协调 |
| 历史FCFF／ROIC | [经营营运资本](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/noncashwc.htm)排除融资及非经营项目；回报需可用资本分母 | 历史FCFF留空；未来净再投资不再被当成缺失期初资本的替代品 | 修正M4缺资本仍生成隐含ROIC的问题；完整历史分类仍缺，研发/租赁修正未被冒称公司经济ROIC |
| 预测FCFF | 税后经营收益减净再投资 | NOPAT−Δ收入／资本效率，不再另扣现金购建支出 | 十年逐年独立Decimal复算；负净投入作为释放资本的假设保留，未认定现金必然可回收 |
| WACC | [资本成本](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/lectures/val.html)与现金流风险、币种及融资权重一致 | 人民币国债减主权利差、行业Beta、成熟ERP＋国家风险；行业D/E重新加杠杆，信用档债务成本，固定终值WACC | 数据链及算式已接入；两例是行业目标权重，BBB、中国暴露100%、固定风险均为政策，不是公司市场WACC |
| 终值 | [稳定期再投资率g/ROIC](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/termvalueexreturns.htm)；折现率大于增长，长期增长／回报／风险共同论证 | 终值NOPAT×(1−g/ROIC)/(WACC−g)，再折现十年 | 公式及无效输入拒绝通过；两例g=2%、ROIC=10%为政策，显式期与稳定期投入率切换单列，未自动调参抹平 |
| 股权／股本 | [每股价值](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/littlebook/valuepershare.htm)应处理非经营资产、债务、少数权益及稀释 | 现金回收情景−经营现金储备−债务账面代理−少数权益账面代理−附加索偿，除显式稀释股数 | 各分量已核对且只扣一次；FN52未拆分、未分类金融投资不计值、转债不转股及期权未定价限制结论 |

两家公司均没有启用研发资本化、额外租赁重分类或期权定价，M3历史FCFF仍空。因此本轮不把共享研发算法的既有回归重复算作两家公司启用研发的验收。历史利润／资本范围证据复用[安克资本分量](../continuing-operations-five/anker-capital-components.md)、[苏泊尔资本分类](../continuing-operations-five/supor-capital.md)，不重复下载原文。

## 两家公司结果

以下均为同一2026H1财务、原信息截止及行业条件政策；人民币元／股，不是当前目标价。

| 指标 | 安克 | 苏泊尔 |
| --- | ---: | ---: |
| 修正前／后每股条件值 | 126.1378／126.1378 | 43.2526／43.2526 |
| 行业目标权重WACC | 8.0754% | 5.7531% |
| 参考收入资本比 | 2.4835 | 2.2730 |
| 首年FCFF（百万元） | 166.7654 | 1,886.7984 |
| 第十年FCFF（百万元） | 8,328.2176 | 1,669.3218 |
| 终值现值／经营价值 | 81.6719% | 63.3432% |
| 仅资本效率翻倍情景（元／股） | 150.3440 | 43.2700 |
| 仅终值ROIC=WACC情景（元／股） | 119.8340 | 38.0005 |

安克早期增长带来较多投入，资本效率翻倍令相同收入所需净投入减半，条件值提高约24.21元；苏泊尔前六年收入收缩对应释放资本，效率提高也会减少这部分回收，十年正负投入现值相抵后仅提高约0.02元。完整逐年NOPAT、投入、FCFF与桥接在回执中，不把效率情景视为新公司证据或推荐预测。终值ROIC对照复用已交付机制，两家公司仍有显著远期假设依赖。

前后实际请求和解析输入完全相同；完整报告只改变十年隐含ROIC与终值隐含ROIC，其他报告字段逐项相同。旧运行保持不变，新run ID可在回执和统一CLI证据位置找到。旧报告含本次错误ROIC时，历史比较／到期核验CLI的严格重放会拒绝；需用原请求重新估值，再引用新run ID，不能覆盖旧报告。既有2025H1归档回归同时锁定这一拒绝与重新估值后收入／利润误差完全不变。修正了错误诊断，没有声称估值准确度因此提升。

## 本轮修正与输出

M4原先以缺失权益／债务／现金为零，累加未来再投资后输出非零“隐含ROIC”。现在只在资本分量完整且有限时建立路径；缺失分量使十年及终值隐含ROIC留空，明确零值仍有效，非正资本分母的回报亦留空。此指标只是基于输入资本的模型诊断，即使可计算也不等于公司经济ROIC已核验。FCFF、折现和股权价值不依赖该诊断，因此有效条件值不受此修正影响。

`api.alphalake.evaluate`在通用账面／历史／校准DCF结果中增加`method_assessment`，统一公司CLI原样透传。分别列明标准事实血缘入口、经营利润口径、预测政策、资本效率来源、历史缺项、资本释放年份、WACC来源、末年及终值投入率、终值占比、股权桥接范围和五类未闭合事项。专项或仅经营价值模型返回null，不冒称它们已通过本轮检查。旧运行不可变；重新估值生成新引擎版本和run ID。

必需财务／时点／源位／政策校验沿用既有拒绝；上述经济缺口仅允许在已有显式条件政策下展示，不自动批准完整公司估值，也不静默选择其他模型。全市场覆盖率未重算。

## 证据与复现

两份`*-request.json.gz`是修正前从主库实际统一CLI保存的完整标准请求，确定性gzip合计约78KB。含源事实ID、原始float32位、期间／单位、公告可用时点、参考发布与政策；它们是标准导出快照，不是原始TDX包或PDF。政策从[固定五家基线](../../../docs/acceptance/valuation-baseline-five-20260911.json)两份既有请求复制，未重新批准行业映射／增长。实际主库只读前后运行与差异见[验收回执](../../../docs/acceptance/method-closure-20260912.json)。

本轮财务期2026-06-30、信息截止2026-09-10T04:42:23Z、股本基期2026-06-30，数据库为`workspace/auto-valuation-20260909/market.duckdb`。这是2026-09-12重跑固定历史截止，不是当天目标价，也不声称这些后来保留的快照构成严格历史PIT。两家公司为固定范围2/2，无新筛除；原五家中的另外三家不在本轮两公司重跑分母，原阻断未解除。

在仓库根目录，使用已安装后端依赖的Python：

```bash
PYTHONPATH=valuation/backend python -m pytest valuation/backend/tests/test_method_closure.py -q
PYTHONPATH=valuation/backend python -m tools.verify_nonfinancial_dcf /absolute/saved-run.json
```

若本机仍保留主库，可从两份快照提取原绑定政策，再重跑统一CLI（数据库只读；不会从快照替代公司财务查询）：

```bash
python - <<'PYREPLAY'
import gzip,json
from pathlib import Path
root=Path('valuation/research/method-closure-20260912')
assignments={}
for code in ('300866','002032'):
    req=json.loads(gzip.decompress((root/f'{code}-request.json.gz').read_bytes()))
    assignments[code]={k:v for k,v in req.items() if k!='data'}
Path('/tmp/alphalake-method-policy.json').write_text(json.dumps(dict(
    policy_version='method-closure-fixed-2026H1',review_note='重放既有条件政策，不重新批准假设',assignments=assignments)))
PYREPLAY
PYTHONPATH=valuation/backend python -m tools.company_valuation \
  workspace/auto-valuation-20260909/market.duckdb 300866 \
  --period 2026-06-30 --as-of 2026-09-10T04:42:23Z \
  --policy /tmp/alphalake-method-policy.json --alphalake /absolute/alphalake
```

将代码换为002032可重跑苏泊尔。未来数据库内容／语义变化可能改变输入，应比较完整请求，不能承诺沿用原价格；本轮前后实际请求已精确比较相同。

`verify_nonfinancial_dcf`从标准事实分量重算窗口，再从原政策和参考观察重建WACC、增长、资本效率、十年NOPAT／净投入／FCFF、终值和股权桥接，不导入估值引擎。采用明确十进制容差，避免float64聚合顺序假设。该工具限定两类十年通用政策及固定／目标／行业权重WACC，其他分支显式拒绝；不代替生产时点及源语义校验。

离线回归进入现有pytest CI：两公司重放幂等；FCFF／现值／WACC篡改拒绝；债务必需项缺失、终值非法和错误政策报告期拒绝；资本效率倍率单因素对照保持财务／增长／利润／WACC／桥接不变。相关已有生产集成测试覆盖真实Go归档→解析→物化→查询及统一摘要。CI消费两份标准快照，不依赖本机主库，不冒称CI重新生成苏泊尔原始财务链。

本轮停止条件：方法表完成、实际断点修复、两公司主库前后对照和独立复算通过、缺项／反向拒绝及文档完成。剩余公司数据及政策依据如表保留，不开启新的预测实验。

## 验证结果与限制

Go全套测试及构建通过；无Go、依赖或迁移修改。Python全套首轮420通过、2项旧报告断言失败、4项既有外部样本缺失跳过、7条警告（590.62秒）。两项断言经限定ROIC变化与可重建运行元数据修正，相关历史3项通过；最终再运行原失败2项及两公司新回归，共4项通过。按全套首轮与修正后复验合计覆盖422个通过项及4个既有跳过，没有声称第二次全套运行。新增测试沿用现有CI，不依赖环境变量启用。

安克既有四份年报180个金额及7个资本开支金额、苏泊尔既有52个原文金额及12个源值验证器重新通过；范围仍为原台账，不计新增原文覆盖。文档本地链接、JSON与`git diff --check`通过。

前端改动仅同步可空ROIC列表类型，类型文件单独TypeScript检查通过。完整前端编译仍有25项既有错误；将该类型文件临时恢复HEAD后强制编译，退出码2及错误输出逐字相同，随后恢复本轮改动。未声称完整前端构建通过，未扩展修复上游UI。本轮没有新增依赖或扩大前端接入范围。
