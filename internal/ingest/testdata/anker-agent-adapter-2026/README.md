# 安克 × Investment Valuation Agent 修正复验

目标原版本 `c4b86e5` 的实际实验发现：缺少季度资本开支仍保留全年值、历史营运变动缺失仍计算 FCFF、没有显式 TTM 入口。原始实验见 AlphaLake 提交 `13a59f8`。本目录现验证目标修正提交 `7ce156a`，目标代码位于 `workspace/Investment_Valuation_Agent`，是单独的 Git 仓库。

## 数据链与实际入口

默认先执行相邻 `valuation-chain-2026/verify.py`：实际 Go 生产查询、PDF 独立复核、显式补充和模型重建通过后，才消费 `facts.csv`、`windows.csv`、`inputs.csv`、`input-audit.csv`，四文件 SHA256 进入输入血缘。没有复制 PDF 解析、覆盖 TDX 数值或补零。

安克代码 300866，信息截止 2026-09-06 UTC 零点，余额日 2026-06-30，TTM 区间 2025-07-01 至 2026-06-30，范围仍为 `provider_default`。标准元/股转换为百万元/百万股，转股价保持元/股；不二次乘万元倍率、不恢复源 float32 精度。

`request.json` 使用实际 `CompanyValuationInput.prepared_ttm` 契约，封装为 `{ "inputs": ... }`。脚本通过真实 Pydantic JSON 往返，再调用 `run_full_valuation` 的 M1–M6 全链。prepared_ttm 的期间、币种、单位、信息截止和来源清单显式传入；不放入年度列表、不通过 K=0 隐藏二次转换，也不再用 None 伪造 CashFlowMetrics 参数。

本轮验证共享编排器和 JSON 契约，未启动 HTTP 服务，不声称 HTTP 响应/错误码或界面操作已验收。目标还没有新增 TTM 界面编辑控件。

## 修正后的结果

- 六个真实季度的收入仍正确旋转：TTM 34,252.649984 百万元。
- 资本开支缺少对应单季度分量时返回 null；标准已计算 TTM 319.634280 百万元可由 prepared_ttm 提供。不再冒用全年 365.120160 百万元。
- 季度总数不足时拒绝计算；最新季度余额缺失时不再借用全年余额。
- M3 直接调用与完整入口的历史 FCFF/FCFE 均保留 null；依赖缺失再投资率的倍数也为空。真正披露/明确输入的零仍可计算。
- 股权桥接缺项或股数无效时，不发布每股值；M6 继续传递空值。安克样本的 cross_holdings 未声称存在通用等价，所以共享入口最终每股值为空，不能将其简化桥接当作完整估值。

预测模块仍可使用明确假设计算经营价值。脚本独立复算十年收入、EBIT、再投资、FCFF 和终值折现；然后在独立政策层沿用 AlphaLake 原桥接，包含现金储备、投资折价、其他索偿、少数股权、转债两路径及股数压力。

这套“目标预测＋原桥接”组合模型仍为 **101.64 元/股**，原 AlphaLake 中间情景为 **99.21 元/股**，均不是当前市场目标价。差异对应增长、利润率、税率和再投资路径，未通过改变源事实消除。目标 API 本身不输出 101.64 元，也没有声称这些安克政策已泛化入目标引擎。

WACC 9%、RF 3%、ERP 6%、终值增长 3%、ROIC 12% 都是实验假设，非市场观测；直接 WACC 分支不使用行业 beta 或市场权益，容器零值不作为来源事实。研发费用化、租赁已经计入，不再次资本化。历史经营营运资本分类、期权实际定价等缺口保留。

## 复现与范围

使用 Python 3.12、pydantic 2.12.5、pypdf 6.17.0、Go，目标检出锁定版本：

```sh
workspace/anker-agent-adapter-20260906/venv/bin/python internal/ingest/testdata/anker-agent-adapter-2026/verify.py
```

`--write` 经验证后重建 request.json/result.json；默认逐字比较。依赖仅安装在 workspace，两个项目的依赖文件未改变。安装时使用网络，证据校验消费本地文件。外部目标检出未纳入 AlphaLake CI，本实验仍是本地复验，不声称默认 CI 已覆盖。

目标后端 115 项测试通过，4 项外部数据测试跳过。新增回归覆盖 prepared TTM JSON 往返、期间/币种/单位/时点约束、二次旋转拒绝、缺季、缺项及显式零；旧正向合成测试明确填入其原本假设为零的少数股权/跨持股，另有删除字段的负向断言。

前端同步 nullable 类型和可选 prepared_ttm，未扩大界面功能。原锁文件有 Vite/Tailwind peer 冲突，安装使用 `npm ci --ignore-scripts --legacy-peer-deps`；构建仍失败，原提交和修正后各 25 条 TypeScript 错误，去除行列号后的错误集合相同，无本轮新增错误。这一限制未计作构建通过。
