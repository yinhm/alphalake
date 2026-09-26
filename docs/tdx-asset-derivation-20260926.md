# TDX现金与投资推导：账面小计和估值范围分开

本轮按用户要求，只用TDX标准数值推导；已有CNINFO原文用于对比，不进入导出金额。已实现可重复的三表诊断工具，并完成安克三期真实主库查询与原文核对。**没有把诊断小计写进原SQL总额，没有解除网页的两项范围阻断，也没有产生新估值。**

## 可以直接计算什么

输入为`financial-statements --include-evidence`的规范三表。工具按通用字段、同一报告期、元单位计算，保留每项标准证据；不重建源编号词典，也不读取PDF金额。

| 计算 | TDX标准字段 | 含义与限制 |
|---|---|---|
| 货币资金与交易资产合计 | monetary_funds + trading_financial_assets | 账面算术合计；含受限资金、可能的套期资产，不是已认证可加回额 |
| 现金等价物与交易资产合计 | cash_and_cash_equivalents + trading_financial_assets | 算术合计；需核对投资与现金等价物的重叠，也可能遗漏其他科目内的存款 |
| 货币资金与现金等价物差额 | monetary_funds − cash_and_cash_equivalents | 未分类差额，不能命名为受限现金 |
| 长期投资组成 | long_term_equity_investments、debt_investments、other_debt_investments、other_equity_instrument_investments、other_noncurrent_financial_assets | 全部分量可用才返回公式值；否则仅列已知小计和逐项状态，源零不补零 |

另列其他流动资产、一年内到期非流动资产、衍生金融资产、其他非流动资产，以及旧准则投资科目和现金流补充表现金组成。旧新科目不无条件相加。即使算术完整，也不自动批准经营属性、公允价值或与经营收益剔除的一致性。

## 真实三期结果

数据库`workspace/alphalake.duckdb`，证券300866，信息截止2026-09-26T03:31:04.747799Z。以下均为TDX标准值推导，单位亿元：

| 报告期 | 货币资金＋交易资产 | 现金等价物＋交易资产 | 货币资金减现金等价物 | 已知长期投资小计 |
|---|---:|---:|---:|---:|
| 2025H1 | 45.8330 | 43.3196 | 2.5134 | 18.4969 |
| 2025年末 | 61.2351 | 55.3474 | 5.8877 | 11.7877 |
| 2026H1 | 60.8677 | 52.7533 | 8.1144 | 12.2160 |

三期长期投资小计均只包含可用的长期股权投资和其他非流动金融资产；债权投资、其他债权投资及其他权益工具投资保留源零歧义。小计不称全部长期投资，更不是市场价值。

对三期货币资金、交易性金融资产、长期股权投资、其他非流动金融资产共12项，与已有PDF主表重提取对比：11项直接按元/万元转float32与标准值相符；2025H1其他非流动金融资产标准值1,265,635,468.75元、原文1,265,635,523.89元，差额−55.14元。先把万元数舍入两位再编码可复现该值，但只是精度解释，不把它推广为通用规则，不覆盖源值。对比收据完整保留差异。

## 原文核对发现的真正缺口

复用安克2026H1已有原文（[文件清单及哈希](../internal/ingest/testdata/anker-valuation-2026/reports.json)）：

- 第107页“其他流动资产”含银行定期存单1,214,315,026.71元，同时含待抵扣进项税554,294,833.09元等。所以只加现金/交易资产会漏掉存款，把其他流动资产全加回又会混入经营资产。
- 第98页货币资金包含一年内定期存款及应计利息632,082,758.46元，也有多种保证金。货币资金与现金等价物的差额不能全部解释成受限现金。
- 第99页交易性金融资产含未到期远期外汇合约1,890,858.07元；独立衍生金融资产科目的远期合约是249,301,303.93元。两者不相等，不能拿独立衍生科目整桶冲减交易资产。

这些金额只用于证明候选公式的边界，没有参与工具推导。已定义的官方目录未提供上述存单、其他流动资产内税项、货币资金内定期/受限分量的独立标准字段；证券类“存出保证金”不能替代普通工商企业受限资金。现金流发生额不能直接解出期末混合资产的分类余额，资产负债表恒等式也不能唯一分解一个混合总额。不能从这些已知总额推出唯一精确解；未知源位置不猜语义。

依据为仓库已归档的[TDX量化目录](../internal/source/tdx/financial/testdata/official-financial-fields-20260925.receipt.json)及[专业财务目录](../internal/source/tdx/financial/testdata/official-profinance-fields.receipt.json)；本轮在线读取失败，未冒称重新取得官方文档。达摩达兰[每股价值桥接](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/littlebook/valuepershare.htm)要求在经营现金流未包含对应收益时加回现金及投资；它不意味着所有账面金融资产都可无条件加回。现有模型直接加回这两列，现金列还参与投入资本，因此分类差异会影响实际结果。

## 复验与后续边界

```bash
# 主库查询按AGENTS要求在独立1GiB限额服务内运行。
./alphalake financial-statements workspace/alphalake.duckdb 300866 \
  --period 2026-06-30 --as-of 2026-09-26T03:31:04.747799Z --include-evidence \
  > workspace/derived/tdx-asset-derivation/2026-06-30.json
PYTHONPATH=valuation/backend .venv/bin/python -m tools.derive_tdx_assets \
  workspace/derived/tdx-asset-derivation/2026-06-30.json \
  > workspace/derived/tdx-asset-derivation/2026-06-30-derived.json
```

动态查询、推导、12项对比及运行日志仅在`workspace/derived/tdx-asset-derivation/`。工具只依赖Python标准库；原文反例回归使用CI已有哈希锁定的pypdf，不新增依赖。相关Python回归10项通过，覆盖缺项不归零、全部缺失不出零、单位篡改拒绝及真实原文反例；Go全套测试、CLI构建通过（首次隔离服务缺GOPATH，补上原有路径后成功，失败日志保留）。未修改前端、主库或正式SQLite。

本轮停止在可识别的TDX账面组成。若未来采用可规模化的账面代理，应显式规定混合资产的纳入/剔除以及相应收益处理，并将其标成估值假设；不能作为完整公司事实自动放行。用户未批准PDF数值补充，本轮没有实施。下一步不再逐公司追公告，而应讨论是否接受有已知偏差的TDX账面代理口径。
