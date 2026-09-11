# 再投资与研发历史输入盘点

状态：源数据扩展、2017年定向补采及三起点连续历史盘点完成；[旧期语义抽核](rd-semantics.md)确认七个正值及一个源零反例，非全样本审核，尚无增长预测规则或有效性结论。

沿用收入复合增长研究的600家开发样本、34份2018Q1–2026H1原始包，使用既有 `cmd/prepare-tdx-history` 增取36个源字段，包含FN304。配置见 [study.json](study.json)，盘点见 [source-inventory.json](source-inventory.json)。未引入新解析器、依赖或标准事实。

源快照本地保存在 `workspace/tdx-capital-inputs-source-20260911/`。可用原始清单与配置重建到一个不存在的新目录：

```bash
go run ./cmd/prepare-tdx-history \
  valuation/research/tdx-capital-inputs/study.json \
  workspace/tdx-loss-recovery-source-20260911/source-manifest.json \
  workspace/tdx-capital-inputs-source-replay
```

本次检查34包SHA-256、MD5和大小，以及配置哈希；与原600家快照按记录身份及原12字段位值进行多重集合比较，221,016个原字段值与重复次数均保留。18,418条源记录包含25条重复键的超额记录，不能字典覆盖后声称唯一。后续时点查询仍须拒绝歧义。

FN304年度源记录正值数2018–2025分别为527、556、565、567、567、575、576、573；这不是可用公司的分母，逐年公司数、重复及零值见盘点。没有在本步实施FN314截止、连续历史准入或旧期语义核验，不能声称已完成研发资本化供给。源零保持歧义，缺期不补零。

## 达摩达兰依据与研究边界

[增长决定因素](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/growth.htm)把增长与再投资规模和回报联系起来：回报稳定时，税后经营利润增长率为再投资率乘资本回报率；存量资产效率变化另有贡献。用于未来新投资的回报不能无条件等同于历史账面平均ROIC。

[研发资本化论述](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/R%26D.htm)将跨期受益的研发视为资本投入：同时调整经营利润、累计未摊销研发资产、摊销和再投资，兑现周期影响摊销年限。原文同时保留税务费用化效应，不能简单给调整后EBIT乘统一税率。研发只是再投资的一部分，不支持“当期研发越多，下年收入必然增长越快”。

本项目 [ADR 013](../../../docs/decisions/013-cashflow-research-fields.md)中的FN304仅为合并利润表已费用化研发，不是研发总投入。已有资本化开发支出需要单独识别，防止重复加回；2018年以来源字段出现正值也不自动扩展标准映射的已审核期间。

接续研究先补齐各历史起点可用的研发支出序列、费用化/资本化范围及经营资本分量，再冻结候选及比较门槛。研发兑现滞后与行业寿命是显式假设；不得按未来结果挑年限、删亏损公司或强迫全部行业采用研发驱动。收入增长和经营利润增长分别检验，与现有短期基线、零增长对照比较，通过开发门槛后才使用新留出。

当前未计算新的ROIC、FCFF、增长率或估值；没有把本次源盘点作为完整资本口径或预测有效性的证明。

## 连续年度及时点盘点

已新增 [年度盘点工具](../../backend/tools/audit_tdx_rd_history.py)，复用既有FN314日期和float32解码。每个H1起点以当年9月1日中国零点为截止，核对上一完整年度及其前五年；三年寿命需要四个年度，五年寿命需要六个年度，才能同时计算当年摊销与期初/期末研究资产。这里盘点的是完整年度，未把年度资料拼作H1 TTM研发资产。

| H1起点 | 公司分母 | 三年寿命所需源序列齐全 | 五年寿命所需源序列齐全 |
| --- | --- | --- | --- |
| 2023 | 600 | 536 | 156 |
| 2024 | 600 | 548 | 520 |
| 2025 | 600 | 557 | 549 |

“齐全”仅指每个所需年度有唯一记录、FN314在截止前可用、FN304为有限正值；仍未逐公司审核语义。零值、缺失、重复与迟到均保留，补采后的记录级诊断见 [结果](rd-history-2017-result.json.gz)及[摘要](rd-history-2017-summary.json)；[首次盘点](rd-history-summary.json)仍保留原0家结论。寿命3/5是供给盘点窗口，不是已采用的行业寿命，也未依据预测效果选择。

2023起点的上一完整年度为2022，五年寿命需要2017–2022。首次档案始于2018，故当时覆盖为0；现已从TDX官方HTTPS地址补采匹配清单的2017年末包，大小4,207,879字节、MD5及二进制解析通过。600家中找到539条记录，157条FN304正值、382条源零；加入连续历史和截止门槛后仅156家齐全。不能把剩余公司补零，或事后只选2024/2025就宣称完成原三起点验证。

仓库根目录复现（输出目录需自行选择；不覆盖不可变原始快照）：

```bash
cd valuation/backend
../../workspace/anker-agent-adapter-20260906/venv/bin/python -m tools.audit_tdx_rd_history \
  ../research/tdx-capital-inputs/study.json \
  ../../workspace/tdx-capital-inputs-source-20260911/snapshot.json
```

结果记录源快照、配置、工具与共用源助手哈希。新增回归验证最早摊销年度必需、未来记录不影响起点、重复/零/缺失/迟到拒绝；测试使用小型输入，600家实测依赖本地归档，未冒称CI每次重放该25MB源快照。

## 共享估值路径口径

[研发重分类修正](../../../docs/damodaran-method-audit-20260911.md#研发重分类的税项与期初资本)已统一M3、公司ROIC与M5的税后经营利润，保持分析重分类前的税项；本期M3期初研发资产及公司边际资本已按研发资产滚动关系修正。真实安克三档情景与合成扩张/收缩回归覆盖这些变化。

[多年历史研发算法](../../../docs/damodaran-method-audit-20260911.md#逐年研发资产与历史回报)现已使用逐年连续投入重建资产和摊销，缺历史留空，不复用当前研究资产。期初租赁PV、历史税项代理及完整经营资本仍未闭合；旧期源语义和未来研发兑现周期仍需验证，不能将算法修正当作新增长预测规则通过。

600家真实源快照另完成[正负向检查](rd-history-checks.json)：全结果重放一致；把2024年末之后记录的FN304/FN314改成非法位值，所有起点结果不变；把一条原本可用的2024年度FN304改零，2025起点五年覆盖准确减少一家，前两起点不变。篡改仅在内存副本进行。首次盘点时本地未找到2017年末包，旧清单MD5不同；本次已成对保存新取得清单与匹配包，下载收据和正负向检查见下。

本轮验收：Python 3.12全套328通过、4项既有外部测试数据缺失跳过；Go全套测试及构建通过。新回归已纳入既有pytest/CI目录，不增加解释器依赖。文档链接、结果哈希及 `git diff --check` 通过；无生产模型、迁移或依赖变更。


## 2017年补采复现

[增量源快照](2017-source.json.gz)保留完整源位及原始包哈希；[下载收据与检查](2017-source-checks.json)记录URL、实际取得时刻、清单与包SHA、篡改拒绝。新原始包保存在 `workspace/tdx-rd-2017-source-20260911/`，旧34包与旧结果未覆盖。后来取得的数据仍只用于经授权的简化回溯，不是严格PIT证据，也未生成标准事实。

在仓库根目录可用既有Go解析器重建该单包到新目录：

```bash
go run ./cmd/prepare-tdx-history \
  valuation/research/tdx-capital-inputs/study.json \
  workspace/tdx-rd-2017-download-20260911/source-manifest.json \
  workspace/tdx-rd-2017-source-replay
```

合并仅用于只读盘点，不重新下载或复制旧34包。于 `valuation/backend` 使用同一Python环境执行：

```python
import gzip, hashlib, json
from pathlib import Path
from tools.audit_tdx_rd_history import audit
p = Path('../research/tdx-capital-inputs')
b = Path('../../workspace/tdx-capital-inputs-source-20260911/snapshot.json').read_bytes()
assert hashlib.sha256(b).hexdigest() == json.loads((p/'rd-history-summary.json').read_text())['evidence']['snapshot_sha256']
s = json.loads(b)
x = gzip.decompress((p/'2017-source.json.gz').read_bytes())
assert hashlib.sha256(x).hexdigest() == json.loads((p/'rd-history-2017-summary.json').read_text())['evidence']['additional_source_sha256']
extra = json.loads(x)
assert extra['study_sha256'] == s['study_sha256']
s['records'] += extra['records']
s['artifacts'] += extra['artifacts']
r = audit(s, json.loads((p/'study.json').read_text())['samples'])
expected = json.loads(gzip.decompress((p/'rd-history-2017-result.json.gz').read_bytes()))
assert all(r[k] == expected[k] for k in r)
print(r['summary'])
```

本次实际全结果重放通过，内存篡改单条2017正值为零后2023五年覆盖156→155，后两起点不变；另将原包翻转一字节，既有Go工具以MD5不符拒绝且未发布快照。临时篡改文件已删除。没有修改代码、依赖或迁移，未重跑全套测试；新增数据验证在本地完成，CI仍只覆盖既有小型年度边界回归及此前已入库样本，不声称CI直接重放本次全部原始包。
