# 港元兑人民币中间价

`go run ./cmd/alphalake sync-hkd-cny market.duckdb` 从[国家外汇管理局](https://www.safe.gov.cn/AppStructured/hlw/RMBQuery.do)下载完整列表，复用参考发布事务进入 `market.fx_rate`。`--offline` 核验归档重放，不触网；`--python`、`--parser` 与既有参考命令一致。重复内容不新增版本，失败保留运行与诊断。

仅审核港元列。按[外管局标价说明](https://www.safe.gov.cn/safe/rmbhlzjj/)，100 港元兑人民币原值除以 100，标准方向为一港元兑人民币。中间价不是收盘价，`fixing_code=cfets_central_parity_safe`、`rate_type=midpoint`；日期用上海零点作载体，不代表报价成交时刻。发布可用时间保守采用首次取得时刻，历史列表不能倒填成当时本系统已经知道。

2026-09-09 完整响应固定在 `internal/source/safe/testdata/rates.html`，SHA-256 `d1193cbcc6a1c825efe6146434374188ce7b3355a43b416573288101e121e8c0`；十行包含 8 月 31 日 86.51，即 0.8651 CNY/HKD。来源 credit：国家外汇管理局、中国外汇交易中心。接口不保证全历史可得；日期缺失应报错，不可任取最新一行。

真实归档纳入既有 `TestBetaYieldRealArchiveReplay` CI 路径，验证在线、本地离线、坏响应、幂等、重开持久化。源测试另锁定货币列和 100 倍换算负向断言。
