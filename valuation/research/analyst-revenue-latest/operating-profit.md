# 同六份研报的收入与营业利润配对检验

先在`b93d3b6`冻结[计划](operating-profit-plan.json)，复用已选定的两家公司、2023/2024/2025三个起点及同六份PDF，不更换券商、窗口或评级。结果是有限改善：同分母下两项均值较低，但2025起点两项均变差，不能据此替换估值预测。

## 覆盖先于误差

六份附表均有三个年度营业利润预测，共18项。初轮冻结快照缺安克2020历史列，结果保留在[初轮结果](operating-profit-result.json)：9项可评价、6项未来、3项阻断。

随后在`9f254ce`冻结[缺口补齐计划](operating-profit-completion-plan.json)，复用已有2020年TDX原包，经现有Go解析器提取[单记录补充源](operating-profit-history-source.json)，保留原取得时间、列表及原包哈希。补充仅用于缺失历史锚点，禁止覆盖原快照；13个印刷历史金额现全部匹配。未改变报告选择、公式或全部18项预测，原9项实际值与误差逐项不变。

[完整结果](operating-profit-completed-result.json)为12项可评价、6项未来，对应6个公司/实际年度，并非12个独立样本。收入指标恢复与原收入研究相同分母；不能将初轮9项与本轮12项指标差异解释为预测改善。

## 同口径结果

营业利润为报表FN86，不是调整EBIT。配套基准使用原研究的FY收入桥接乘起点TTM营业利润率（FN86/FN230）；这是显式的配套基准，不冒称生产模型已输出营业利润预测。研报使用同份原文的收入与营业利润预测。实际营业利润取对应FY的TDX FN86，实际收入沿用四季FN230加总；FN314及原取得时间门控复用既有函数，不将年报FN230当全年数。

| 范围 | 可评价位置 | 研报营业利润误差/实际收入 | 配套基准 | 研报收入MAE | 原收入桥接MAE |
| --- | ---: | ---: | ---: | ---: | ---: |
| 合计 | 12 | 1.1121% | 1.2424% | 8.0947% | 10.3948% |
| 2023起点 | 6 | 1.1936% | 1.3875% | 11.0711% | 13.3337% |
| 2024起点 | 4 | 1.2520% | 1.4008% | 5.3536% | 10.0545% |
| 2025起点 | 2 | 0.5878% | 0.4903% | 4.6477% | 2.2589% |

营业利润WAPE为研报10.4743%、配套基准11.5190%。两家公司营业利润各自总体均值较低，但安克收入误差11.3774%高于原桥接10.5274%，仍不足以抹掉2025起点退化；不按公司、年份或科目事后组合赢家，不再扫描这个选报规则的参数。

本轮只核验了营业利润预测，其余调整EBIT分量尚未完成预测口径审核，不能全部声称不存在或归零。即使营业利润表现较好，也不能跳过利息、非经营损益、税项及再投资而生成新DCF，更不能将12个位置外推至五年增长或终值。原增长/资本需求规则与所有既有失败结论保持不变。

## 来源及复算

[提取定位](operating-profit-evidence.json)保存六页各自的年度列、单位和营业利润行；[结果](operating-profit-result.json)保留历史锚点、全部位置、两路预测及分组指标。原报告URL、原取得时间、PDF哈希、日期与身份核验沿用[sources.json](sources.json)，本轮不重新声称独立身份审核。

表格包括左右并排的资产负债表/利润表，以及金额/增长率同名行。提取只匹配精确“营业利润”标签后的指定数量金额，要求年度E列与百万元单位同页；百分号行不匹配。印刷历史列按整数舍入与源精度比较，不用研报数字恢复TDX小数。原文后来取得、样本已暴露且年度重叠，不是严格PIT、一致预期或独立留出。

工具位于`valuation/backend/tools/verify_analyst_operating_profit.py`，从仓库根目录执行：

```bash
PYTHONPATH=valuation/backend python -m tools.verify_analyst_operating_profit \
 valuation/research/analyst-revenue-latest workspace/analyst-revenue-latest-20260911 \
 --history-source valuation/research/analyst-revenue-latest/operating-profit-history-source.json
PYTHONPATH=valuation/backend python -m pytest valuation/backend/tests/test_analyst_operating_profit.py -q
```

使用已有后端Python/pypdf依赖，本地真实PDF重提取与结果精确重放通过；金额、单位、E列及PDF哈希篡改均被拒绝。普通系统Python未安装pypdf，应使用项目虚拟环境，不额外安装依赖。既有pytest CI会发现此测试，但裸克隆不含六份券商全文时明确skip；工具本身缺原文即失败，无静默降级，不能声称PDF原文已有完整CI覆盖。未新增全文下载、数据库事实、生产政策或估值；Go全套与构建通过，未重跑无关后端全套。

补充源复现沿用现有工具（目标目录须不存在）：

```bash
go run ./cmd/prepare-tdx-history \
 valuation/research/analyst-revenue-latest/operating-profit-completion-plan.json \
 valuation/research/analyst-revenue-latest/operating-profit-history-manifest.json \
 workspace/analyst-profit-history-replay
```

完整源包仍为本地归档，清理后须恢复清单所列文件并核对哈希；Git内源切片不冒充完整原包。补充源位篡改被拒绝；不传补充参数仍精确重放初轮结果。此研究至此收口，不继续换券商、筛异常年份或调整阈值，不更新生产DCF。
