# 再投资与研发历史输入盘点

状态：源数据扩展与初步核对完成；尚无增长预测规则或有效性结论。

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
