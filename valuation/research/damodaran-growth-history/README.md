# 达摩达兰历史行业基本面增长参考

从[官方历史目录](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/dataarchived.html)实际链接取得三份全球行业表，保留完整XLS、目录原文压缩件与[清单](manifest.json)。观察日期以工作簿单元格为准，不按文件名猜年份；这批文件名后缀22/23/24分别对应目录1/23、1/24、1/25，工作簿日期为2023/2024/2025年1月5日。

| 工作簿 | 表内观察日 | 行业数 | 三指标位置数 | 空值 |
| --- | --- | ---: | ---: | ---: |
| [fundgrEBGlobal22.xls](fundgrEBGlobal22.xls) | 2023-01-05 | 94 | 282 | 8 |
| [fundgrEBGlobal23.xls](fundgrEBGlobal23.xls) | 2024-01-05 | 94 | 282 | 6 |
| [fundgrEBGlobal24.xls](fundgrEBGlobal24.xls) | 2025-01-05 | 94 | 282 | 14 |

三指标是ROC、Reinvestment Rate和Expected Growth in EBIT，均以小数比率保留，零、负值和缺失分别保存；不把经营利润增长改成收入增长。每行另保留行业名、公司样本数和原始行号，单元格C/D/E对应三指标。第8行表头之前的说明和末尾两条市场合计均保留在原文件；行业提取固定为已核对的第9—102行，末尾合计名称按原文留在清单，不计行业数。2025表的“Tiotal Market”拼写亦原样保留，不创建一个新行业。

## 时间与使用边界

三份文件均在2026-09-11首次由本轮取得，清单分别记录精确时间、URL、字节数、SHA以及目录锚点。目录月份、表内观察日、本次首次取得分别留痕；具体独立发布日期未知，不借用其他文件的日期。原始目录保存在[source-index.html.gz](source-index.html.gz)，其哈希和取得时间另记。

这是后来取得的历史版本，不证明本项目当时已留存或上游从未重修，也不构成严格PIT。可以为显式简化回溯提供对应年份的行业参考，不能回填成当时可用的生产标准事实。尚未发布AlphaLake参考库，没有修改公司政策、估值或历史财务。

行业基本面增长是提供者的估计，不能未经说明直接认定为某公司的下一年或长期增长；当前公司行业分类也不自动证明历史业务组成相同。后续研究须先固定映射依据、时点代理及候选规则，再评分；已暴露样本仍只作开发，不用2026年行业值代替这些历史表。

两个行业的经营利润增长参考仅作字段例示，不是公司预测：

| 观察年 | Computers/Peripherals | Furn/Home Furnishings |
| --- | ---: | ---: |
| 2023 | 10.0329% | 11.7675% |
| 2024 | 6.1327% | 7.0160% |
| 2025 | 7.0041% | −3.1720% |

## 校验

复用已纠正语义的`parse_fundgr`，所有有值单元格与C/D/E原文逐项核对，缺失为null。回归固定原文件及目录哈希、Global范围、观察日期、94条唯一行业、行号、样本数、三字段和28处缺失；篡改每份文件EBIT列名均拒绝。哈希/重复解析不等于独立验证行业底层公司的财务或提供者预测有效性。

```bash
PYTHONPATH=valuation/backend workspace/anker-agent-adapter-20260906/venv/bin/python -m pytest valuation/backend/tests/test_fundgr_semantics.py -q
```

使用既有Python/xlrd，无新增依赖或skip，测试进入现有pytest CI。本轮只增加历史证据和回归，未改生产解析器；三项对应回归、Go全套与构建通过，没有重复全部后端测试。原三份下载及回执另留本地`workspace/damodaran-growth-history-20260911/`。
