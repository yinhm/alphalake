# 五家公司三年度管理层经营目标复验

状态：**已冻结规则并开始收集，尚未评分。**本试验检验公司公开经营计划能否提供比机械外推更可靠的信息，不把经营目标当财报事实、业绩承诺或已验证预测。

`b89b439`对应的[冻结计划](study.json)先于本轮目标取数提交。固定安克、苏泊尔、格力、伊利、海天，起点2023/2024/2025H1，信息截止为当年9月1日中国零点，评价截止2026-09-10中国零点；合计15个公司/起点，缺目标不能删公司或换报告。先选原上年度年报中对起点年度的明确经营目标，其他券商报告、股权激励考核条件和后来的目标修订不补入。

## 当前来源进度

[目标台账](targets.json)保留全部15个位置。目前取得两份原年报，原文重提取4个目标；对应两项位置为`extracted_selection_pending`，其余13项为`document_pending`。已取得不等于原公告选择和实际口径审核完成，当前准入/评分仍为零。

| 原年报 | 目标年度 | 收入目标 | 利润目标 | 原PDF物理页 |
| --- | --- | --- | --- | ---: |
| 伊利2022年报 | 2023 | 营业总收入1355亿元 | 利润总额125亿元 | 34 |
| 海天2022年报 | 2023 | 营业收入281.7亿元 | 归母净利68.2亿元 | 29 |

来源：[伊利原年报](https://static.cninfo.com.cn/finalpage/2023-04-28/1216664083.PDF)、[海天原年报](https://static.cninfo.com.cn/finalpage/2023-04-26/1216595423.PDF)。两份全文约8.6MB保留于本地workspace；URL、首次取得、SHA及定位随台账入库。伊利页码比印刷页码多4页，不能直接按PDF搜索结果显示的印刷“30”定位。

营业总收入与营业收入分开，利润总额与归母净利也分开；利润概念混合后不产生一个总准确率。即使来源相同，已披露目标也不自动进入DCF的收入/调整EBIT路径。

## 源数据与复验边界

复用现有Go工具及8份既有TDX完整包，补取2022—2025各H1/FY共40条源记录；[切片](snapshot.json)及[回执](source-receipt.json)已保存。与原五家公司资本切片共有的520个位值完全不变；没有新增TDX下载、标准事实、生产映射或既有预测变更。验收后删除约45MB临时重复包，原清单指向的原包保留。

FN74、502、92、95、96按固定mootdx目录定位候选概念，尚需与原财报验证实际期间、倍率及范围。尤其FN502为候选万元编码营业总收入，不能在源零或未审核时退回营业收入，不能用FY包FN230冒充全年。源切片解析成功不算字段语义已验证。

计划先比较当前规则FY桥接及零增长桥接。管理层目标通常早于H1实际信息，机械基线具有信息时点优势，该差异明确保留。通过门槛需至少6个收入位置、两家公司、覆盖三个起点，收入MAE相对两个基线均改善至少5%，WAPE不退化，任一起点MAE退化不超过5%；未通过即停止该来源选择规则，不调整名单、阈值或目标解释来补救。

这些公司已暴露，属于开发研究。即使通过也只允许进入未参与选择的验证；不支持直接更改默认政策，更不证明多年增长、再投资、终值或完整DCF有效。

## 重放

```bash
# 输出目录须不存在；全包依赖source-manifest.json列出的本地原归档。
go run ./cmd/prepare-tdx-history \
 valuation/research/management-targets-five/study.json \
 valuation/research/management-targets-five/source-manifest.json \
 workspace/management-targets-five-replay
PYTHONPATH=valuation/backend workspace/anker-agent-adapter-20260906/venv/bin/python -m tools.verify_management_targets \
 valuation/research/management-targets-five workspace/management-targets-five-20260911
PYTHONPATH=valuation/backend workspace/anker-agent-adapter-20260906/venv/bin/python -m pytest valuation/backend/tests/test_management_targets.py -q
```

现有Go解析器再次核验清单大小/MD5；真实翻转首包一个字节会报`source size/MD5 mismatch`，不发布snapshot。Python检查协议/切片血缘、520个旧位不变、15个位置保留及4个原文金额；金额篡改、删除待取证位置、把目标改称事实均拒绝。使用已有Python/pypdf依赖，既有CI会发现测试，但缺原PDF会明确skip；CLI缺原文失败。本轮相关回归、Go全套及构建通过，无新增依赖。

下一步完成其余13个位置的原公告选择及目标有无检查，再审核同口径TDX实际值，最后按冻结规则一次评分；不以当前两个可提取位置提前下结论。
