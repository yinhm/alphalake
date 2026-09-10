# 一年期利润校准政策

已支持`nonfinancial-history-fcff-calibrated-v1`，从主库标准事实查询，经统一公司CLI/API进入既有DCF引擎。仅第一年利润率乘以校准倍率，收入、第2—10年、再投资、WACC和股权桥接保持原政策；未启用的历史预测政策不变。当前提交的试用配置只批准安克2026H1，不自动替换全市场或专项模型。

## 依据与适用边界

[冻结研究及失败记录](../valuation/research/tdx-growth-expanded/README.md)包含两个历史起点、开发/留出隔离和独立120家公司复验。171/240个组合可评价，一年期利润误差6.9774%→6.2629%，改善约10.2%；其中未检出金融字段的169个组合为7.0389%→6.3350%。逐一删除训练公司后重拟合，最小改善仍约7.14%。收入预测没有因此改善，也不能据此证明完整DCF、公允价值或五年利润路径有效。 随后固定规则的2025起点时间复验仅改善约1.9%，未达5%门槛；不扩大采用范围，不能将两个旧窗口的效果当作跨期保证。

使用的校准是显式研究政策：TDX源数值与FN314简化回溯，保留后来修订和当前名单抽样偏差；不伪造当时已留存数据，不向标准事实表写入研究数值。校准不放宽普通模型的金融业务、身份、缺项或期间检查；茅台的真实FN506仍使普通模型拒绝，不能靠批准代码绕过。

## 生成与采用

在`valuation/backend`，使用安装了后端依赖的Python：

```bash
python -m tools.prepare_forecast_calibration \
  ../research/tdx-growth-expanded/protocol-v7.json \
  ../research/tdx-growth-expanded/snapshot-v7.json \
  ../research/tdx-growth-expanded/holdout-v7-summary.json \
  --period 2026-06-30 --code 300866 > /tmp/alphalake-calibration.json
```

工具重现开发选择和独立验证门槛，再为请求报告期拟合上一年窗口；源或验证结果篡改、样本不足会退出1并输出拒绝JSON。新生成政策的`prepared_at`取实际生成时刻，不回填成训练截止。上例只生成校准对象；采用时将其放入普通历史政策的`calibration`字段，并将`policy_id`改为上述新版本，随后按已有BatchPolicy格式设置公司assignment。

已提交可直接复现的[基线配置](../valuation/examples/nonfinancial-baseline-2026H1-pilot.json)和[校准配置](../valuation/examples/nonfinancial-calibrated-2026H1-pilot.json)：

```bash
python -m tools.company_valuation /absolute/market.duckdb 300866 \
  --period 2026-06-30 --as-of 2026-09-10T22:18:56.906406Z \
  --policy ../examples/nonfinancial-baseline-2026H1-pilot.json \
  --policy ../examples/nonfinancial-calibrated-2026H1-pilot.json \
  --select nonfinancial-calibrated-2026H1-pilot-v1
```

这个固定截止晚于提交政策的生成时间；重新生成政策后必须使用不早于新`prepared_at`的估值截止。跨报告期必须重新拟合和显式批准，缺少训练数据不默认倍率1，过期或未批准的公司不静默降级。

校准对象含模型版本、适用公司、报告期、训练起点/截止、实际生成时间、源快照与独立验证哈希及逐公司训练观察。程序从观察重新计算系数，不能单独改倍率；哈希引用和数学重算不是来源认证或逐公司PDF语义审核，原始研究证据另行归档。当前规则固定半量、尺度[0.5,1.5]和第一年；不允许与未经验证的增长限幅/偏移或利润率偏移叠加。新模块进入引擎版本摘要，完整政策与运行ID随现有估值结果保存。

## 当前期拟合与主库验收

[当前期政策](../valuation/research/tdx-growth-expanded/calibration-2026H1.json)使用2025H1→2026H1的45个开发公司组合，训练标签按2026-09-01中国零点检查可用性。以`预测EBIT / 实际收入`为权重，对`实际EBIT / 预测EBIT`取加权中位数，所得尺度0.5936120276515603；半量应用后的第一年利润倍率为0.7968060138257802。后续修订风险仍存在，不称严格历史PIT。

[主库验收记录](acceptance/forecast-calibration-20260910.json)只计一家公司和两个政策候选：

| 安克普通模型，固定10% WACC | 条件每股值 |
| --- | --- |
| 原历史规则 | 92.1504元 |
| 显式一年期校准 | 91.1626元 |

两端来自同一次标准事实导出。逐项核对财务值、收入及再投资预测、第2—10年、WACC、股权与股本桥接一致。独立Decimal复算`第一年EBIT变化 × (1−税率) / (1+WACC) / 股数`，与每股差额−0.9878169291元一致；原始run ID见验收记录。不是此前安克专项政策数值的更新，不是当前目标价，也不是全市场校准采用率。

真实Go归档→标准物化/查询→HTTP/引擎回归覆盖安克正向、幂等重放、茅台范围拒绝，以及倍率、五年外推、权重、未批准代码、未来生成时间、报告期和基线规则篡改拒绝。研究快照和旧规则结果继续回归。统一比较工具可列出政策和预测变化；当前自动贡献归因仍仅支持既有WACC条件，本次单因素结论来自上述独立验收公式。

下一步仍需检验其他时间区间的稳定性，并研究收入、再投资及终值误差；不能把本轮一年期利润改善扩大为完整估值准确率。
