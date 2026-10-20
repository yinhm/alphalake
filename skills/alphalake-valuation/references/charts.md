# 公司研究的图表交付

完整公司研究读取本页。图表是帮助读者判断业务和价值的证据，不是给文字配装饰。通常选4—8幅，围绕最重要问题取舍；报告已有完整计算时，至少把预测现金流、价值组成、实际情景／敏感性清楚呈现，形式可以是图或更合适的表。不可因缺一个分部数据跳过其余图，也不造数据凑数量。

## 选什么图

| 读者的问题 | 合适图表 | 必须明确的边界 |
| --- | --- | --- |
| 钱从哪里来 | 同期产品／地域／渠道收入柱状图 | 不混用不同期间，不把地域与产品相加；没有分部数据就保留文字／总量 |
| 公司是在变大还是变好 | 可比年度收入、利润或利润率趋势 | 规模与比率分图，FY／中期／TTM分开；报表与研发调整利润率不混接 |
| 利润是不是现金 | 同期净利、CFO及可得资本支出；库存／应收另图 | 不把CFO减资本开支改名为FCFF；余额与流量不串成同一系列 |
| 故事未来要兑现什么 | 已计算收入／利润率路径，预测与历史分开 | 历史实线、条件虚线或不同图；标签写真实截至期间，不把TTM起点预测称自然年 |
| 增长消耗多少现金 | 引擎年度NOPAT、总净再投资、FCFF对照 | 不再扣一次研发，不从图表中另算现金税或FCFF；负再投资保留并解释 |
| 每股价值由什么组成 | 经营现值→现金／投资→索偿→股权的瀑布图 | 全部取已有结果及股权桥；未知不画0，不用残差编“其他调整” |
| 结论对什么敏感 | 实际具名条件柱状图、单因素对照或已有矩阵热图 | 无概率；没有实际矩阵不插值。股本／币种／时点不可比时，不叠当前股价线 |

每图前后写**一句读图结论＋对经营或估值的含义**，并附来源、期间、单位和假设角色。不要只说“如图所示”。正文保留关键数表，图片不可成为唯一可读数据。

## 可复用绘图入口

`tools.research_charts`只绘制给定数据，不读取数据库选参数、不计算DCF、税或WACC，不下载材料。使用Matplotlib的Agg后端生成中文PNG；运行环境需要可选依赖及中文字体：

```bash
# 使用项目已确认的Python／包管理器；不要更换生产解释器。
python -m pip install -e './valuation/backend[research]'
# Linux缺中文字体时安装fonts-noto-cjk；本机已有可用字体无需重复安装。

PYTHONPATH=valuation/backend .venv/bin/python -m tools.research_charts \
  --input workspace/research/example-company/charts-input.json \
  --output workspace/research/example-company/charts
```

按资源约定执行成套研究／验收，不与大库任务并行。工具拒绝覆盖输出及已登记研究，修订另建研究ID；不改前端或开放SVG／任意HTML。

输入契约为`alphalake-research-charts-v1`：

- 顶层`company`、`sources`、`charts`。每个来源有唯一`id`、`label`、workspace相对`path`、实际`sha256`及`locator`（JSON字段／表格、物理页等）。来源必须存在且哈希匹配；源PDF引用规范对象，不复制。
- 每图有稳定`id`、`kind`、`title`、`unit`、`period`、`takeaway`、`source_ids`及分类`labels`。`unit`写明亿元／百万元／元每股／百分比，不靠工具猜测或自动换算。
- `line`／`bar`：`series`中每项有`label`、`role`及与labels等长的`values`。role仅`reported`（已披露）、`forecast`（条件预测）、`reference`（参考／对照）；缺项为null，保留断点／空柱，拒绝全空及非有限值。不同角色可拆序列，不把预测连接成已实现业绩。
- `waterfall`：`values`和`types`与labels等长；type为`total`或`change`，首尾必须是给定总值。工具只核对图中组成加总与给定终点，不用残差修补、不计算股权价值。
- `heatmap`：`row_labels`、二维`values`、列labels；输入必须是已实际计算的数表，工具不生成未计算格子。只有非空矩阵才画热图。

输入准备时从保存的输出直接取数组，避免手抄：预测用`review_native_policy`的`forecast`及原生结果，桥接用`equity_bridge`／`dcf`／`final`的实际字段，条件值用已保存结果。同一比较保留币种、股本、利润和投入口径；有原始值却没有完整组成时，可以改用经营现值组成图，不强画不闭合股权桥。

金额百万元转亿元是展示除100；小数比率转百分比是乘100。转换在准备数据时注明，不改变原始请求／结果，不据图重写估值方法。历史会计数据、PDF中的经营分部与条件预测分别标明来源角色；来源哈希只证明文件内容，不能证明抄录值、单位或经济解释正确。

输出新目录包含各PNG、`chart-data.json`（精确绘图数组及定位）、`manifest.json`（文件哈希、渲染版本）、`gallery.md`（图片及解读）。manifest的`model_calculated=false`明确这里只做展示。

## 接入现有研究页面

在正文合适位置写`![中文图名](charts/revenue.png)`，不要只交图片下载包；`gallery.md`可作为附文。将每张图片、绘图数据及所引用源文件加入现有`research.json`的`files`，使用manifest中的path／sha256。报告发布仍走`tools.research_reports publish/validate`，不另建图表服务或报告schema。

登记前逐图对照输入／结果，检查零点、正负号、缺项、期间、单位、标签和中文字体；尤其检查价值桥闭合及事实／预测标识。登记后实际读取图片URL并在桌面、窄屏打开报告，确认非缺字、可加载、图未横向溢出、来源可点；生成文件不等于已成功展示。
