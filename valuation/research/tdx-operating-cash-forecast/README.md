# 报表经营现金流预测复验

当前开发164/180个组合通过全部门槛，留出尚未评分，未改变生产政策。上轮存货调节预测未胜过零贡献基准；本轮回到可逐期核对的报表OCF目标。生产DCF以收入增量÷资本效率预测总再投资，而报表OCF减资本开支仍包含经营税、融资/投资收益及其他分类差异，不等于FCFF；本研究不以现金代理替代完整估值准确度验收。

[协议](protocol.json)在评分前固定原60家开发公司及资本开支/营运现金研究中仍未评分的120家公司留出，三个起点2023H1、2024H1、2025H1。候选将当前与上年同期的TTM经营现金流率等权平均，乘当前TTM收入；收入固定不增长，不借用未来收入，不裁剪负现金流。对照为沿用当前OCF，另设零OCF基准。所有模型共用沿用最近资本开支，额外检验OCF减资本开支预测的误差是否恶化，避免只改善一个分量。

采用门槛：至少80组合，OCF主要误差较沿用改善至少5%，OCF WAPE及零OCF两指标不恶化，各起点主要误差不超过沿用的105%，逐公司剔除仍不劣于沿用；现金代理的主要误差和WAPE也不得恶化。开发失败不打开留出，不搜索平滑权重；缺项与拒绝保留完整分母。

源数据仍是后来取得的TDX版本，FN314仅作用户授权的简化回溯时间约束，不称严格PIT；当前证券和行业抽样保留幸存者偏差。无需全量CNINFO，新异常才定向原文核验。


## 开发结果与实现冻结

协议先于源提取、评分提交（`deb302f`）。[快照](snapshot.json)复用同一22个原始TDX包，原资本开支研究180家记录及既有字段位模式完全一致，新增FN234原始位模式；无新网络采集，不写标准事实。实现位于[研究工具](../../backend/tools/backtest_tdx_operating_cash.py)，复用现有资本开支期间检查及OCF四季度提取，不更改原研究。

[开发回执](development-summary.json)保留180个公司/起点组合，164个可评价、16个拒绝。仅比较当前、上年同期及目标三期均可取得收入/OCF/非负资本开支的共同集合；这意味着上年同期资本开支即使不进入公式，也遵循同一现金观测检查。负OCF及真实零OCF保留，拒绝不计零误差。

| 开发指标 | 沿用最近值 | 两期现金流率均值 |
| --- | ---: | ---: |
| OCF主要误差 | 13.623955% | 12.040883% |
| OCF WAPE | 72.502607% | 65.469778% |
| OCF减资本开支主要误差 | 14.723769% | 13.862279% |
| OCF减资本开支WAPE | 114.075433% | 105.569594% |

候选OCF主要误差改善约11.62%，零OCF基准为14.568125%，全部冻结门槛通过。先提交实现、开发选择与回归，再使用绑定相同代码/源哈希的回执开启留出；开发通过不代表已经证明预测有效。

```bash
cd valuation/backend
python -m tools.backtest_tdx_operating_cash ../research/tdx-operating-cash-forecast/protocol.json ../research/tdx-operating-cash-forecast/snapshot.json --phase development > /tmp/alphalake-ocf-development.json
python -m tools.backtest_tdx_operating_cash ../research/tdx-operating-cash-forecast/protocol.json ../research/tdx-operating-cash-forecast/snapshot.json --phase holdout --selection /tmp/alphalake-ocf-development.json
```

这条链只检验报表经营现金流及现金代理，不产出实际FCFF。完整总再投资仍需非现金投入、经营/非经营资产范围及税项桥接；后续即使留出通过，也只能以有版本和适用边界的现金预测输出供估值分析，不能直接替换DCF再投资项。
