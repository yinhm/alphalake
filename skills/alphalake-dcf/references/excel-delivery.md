# Linux／Python 独立 DCF Excel

不使用原生导出API、`generate_full_workbook` 或原生DCF结果填表。读[独立计算说明](independent-model.md)，用已有 `.venv/bin/python` 和 `openpyxl` 编写活公式。

## 模型布局

核心两页 `DCF`、`WACC`，必要时加中文“来源与补证”。预测年数取完整条件，不固定十年；先规划行列和情景区域再写公式，避免插行破坏引用。

| 区域 | 内容 |
|---|---|
| DCF输入／条件 | 各时点、TTM组成、报表利润、现金／投资／债务、现有股数及逐年经营、税务、投入、终值假设 |
| DCF年度预测 | 收入、报表／经济EBIT、研发摊销、税基／NOL、现金税、NOPAT、总投入、FCFF、折现及PV |
| DCF终值／桥接 | 正常化NOPAT、g/ROIC投入、研发／税务过渡、经营到普通股权益与每股价值 |
| DCF敏感性 | 生效驱动的5×5完整重算，中心条件和非法组合状态 |
| WACC | 原始Rf、ERP／CRP、Beta、债务成本依据、市场权重、加杠杆及初始／长期融资 |

声明一个主条件；已有多个故事分别保留完整路径，不默认三情景为概率或最可能。日期、单位、币种、冻结窗格、格式、列宽和打印范围便于阅读，不照搬原网页13页。

## 公式与批注

事实、报价、显式假设可硬编码；预测、税务、折现、桥接、敏感性都用活公式，不能把Python已算结果写成数值冒充可编辑模型。Python核算与Excel公式共享数学定义，不共享被测代码或已算结果。

输入字体蓝、页内公式黑、跨页引用绿；中心格突出即可。每个硬编码输入创建时加批注：`来源／条件类型；原单位／期间／合并范围；文件、页码、表项或API字段；哈希／版本；假设依据`。PDF补充和TDX分列，研究判断不伪造公告出处。

敏感轴用实际生效条件，改变终值g时重新计算投入，改变融资保持税盾和权重一致；必要联动明示，不称纯单因素。每格完整DCF，不用线性价格缩放。非法g、非正ROIC、零股数等显示拒绝原因，不用 `IFERROR(...,0)` 掩盖，不留手填占位。

```python
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.workbook.properties import CalcProperties

wb = Workbook()
ws = wb.active
ws.title = "DCF"
wb.create_sheet("WACC")
# 仅演示公式／批注，不是公司默认或完整模型。
ws["B4"] = 1000
ws["B4"].comment = Comment("虚构教学数据：收入1000百万元，非公司事实", "AlphaLake DCF")
ws["C3"] = 0.08
ws["C3"].comment = Comment("虚构教学条件：增长8%，非默认预测", "AlphaLake DCF")
ws["C4"] = "=B4*(1+C3)"
wb.calculation = CalcProperties(fullCalcOnLoad=True, forceFullCalc=True)
```

`fullCalcOnLoad`只要求打开后重算，不表示已重算；实际模型须覆盖本次研发、税务、终值与索偿分支。

## 核验

1. Python独立数值先通过本次算例、现金和价值恒等式，不调用valuation审阅器。
2. 使用实际安装的Linux公式工具，如可用的LibreOffice无界面模式；核对函数支持、版本，用独立临时用户配置和输出目录，不覆盖原文件，不假设固定 `recalc.py` 已存在。
3. 重算后用 `data_only=False/True` 分别读取：公式存在、关键缓存非空、无意外 `#REF!/#DIV/0!/#VALUE!/#NAME?/#NUM!`，逐年与Python比较。预先声明的非法敏感格不混同公式错误。
4. 改一项增长／利润率、一项投入／融资再重算，确认完整链路变化；恢复中心须复现原条件。不只检查能打开或ZIP可解包。
5. 无计算工具仍交付独立Python结果及完整活公式，明确Excel执行核验未完成；不伪造缓存或回退原生快照。用户要求已重算Excel时，解决具体环境缺项后才声明通过。

保存模型定义、输入、环境和源／产物哈希。数学复算一致不代表事实、经济依据或预测有效性已核实。
