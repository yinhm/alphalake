# 来源、致谢与改写范围

本Skill的程序调用与数据边界来自AlphaLake；公司研究、论点跟踪、假设说明和完整交付流程参考并改写自[Anthropic financial-services](https://github.com/anthropics/financial-services)。感谢Anthropic及原贡献者。

固定参考提交：`574ed3624aebd0418c7e96cd101262f30210ab26`；本轮核对日期：2026-10-20。此目录是**中文适配及重组版本**，不是上游原件：入口、references及报告模板均由AlphaLake改写，未复制上游计算器或模板实现。相关改写沿[Apache-2.0](LICENSE.txt)许可保留；许可证与该提交根LICENSE逐字节一致，SHA-256为`cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`。该提交文件树无NOTICE文件。

本轮进一步查阅同提交的[公司研究细则](https://github.com/anthropics/financial-services/blob/574ed3624aebd0418c7e96cd101262f30210ab26/plugins/vertical-plugins/equity-research/skills/initiating-coverage/references/task1-company-research.md)及[制图细则](https://github.com/anthropics/financial-services/blob/574ed3624aebd0418c7e96cd101262f30210ab26/plugins/vertical-plugins/equity-research/skills/initiating-coverage/references/task4-chart-generation.md)：借鉴业务／客户／竞争／治理的覆盖和先有研究、财务、估值再制图的依赖；改为中文网页研究、通常4—8幅关键图，不继承6,000—8,000词、管理层履历配额、25—35图或分五次请求的强制流程。现金与估值图直接消费AlphaLake已保存输出；新增`tools.research_charts`是本地展示工具，不是上游代码或第二套估值模型。

## 核心流程来源

| 固定版本源文件 | 借鉴内容 | AlphaLake适配 |
| --- | --- | --- |
| [initiating-coverage](https://github.com/anthropics/financial-services/blob/574ed3624aebd0418c7e96cd101262f30210ab26/plugins/vertical-plugins/equity-research/skills/initiating-coverage/SKILL.md) | 从公司研究、财务到估值和研究交付 | 统一七步流程；完整任务连续完成，不继承逐阶段确认、固定页数／图表数 |
| [competitive-analysis](https://github.com/anthropics/financial-services/blob/574ed3624aebd0418c7e96cd101262f30210ab26/plugins/vertical-plugins/financial-analysis/skills/competitive-analysis/SKILL.md) | 行业经济、竞争、公司特征与综合判断 | 用会改变估值的量价、成本、市场和资本问题组织研究，不强制演示文稿 |
| [thesis-tracker](https://github.com/anthropics/financial-services/blob/574ed3624aebd0418c7e96cd101262f30210ab26/plugins/vertical-plugins/equity-research/skills/thesis-tracker/SKILL.md) | 论点、支持／反证、观察指标与修订 | 连接经营主张与显式条件，不附带仓位、止损或多空交易建议 |
| [dcf-model](https://github.com/anthropics/financial-services/blob/574ed3624aebd0418c7e96cd101262f30210ab26/plugins/vertical-plugins/financial-analysis/skills/dcf-model/SKILL.md) | 假设出处、现金流、WACC、终值、价值桥和敏感性 | 只调用现有引擎；不继承固定增长／税率、终值占比要求或新增退出倍数分支；Excel按需 |
| [comps-analysis](https://github.com/anthropics/financial-services/blob/574ed3624aebd0418c7e96cd101262f30210ab26/plugins/vertical-plugins/financial-analysis/skills/comps-analysis/SKILL.md) | 可比公司选择、期间及财务口径核对 | 同业用于解释机制和量级，不按股价选参数，不将倍数估值设为必需 |
| [earnings-analysis](https://github.com/anthropics/financial-services/blob/574ed3624aebd0418c7e96cd101262f30210ab26/plugins/vertical-plugins/equity-research/skills/earnings-analysis/SKILL.md) | 财报变化、驱动及原论点更新 | 与已有事实／本研究旧预测比较；无共识不造beat/miss，不要求固定篇幅或订阅终端 |
| [model-update](https://github.com/anthropics/financial-services/blob/574ed3624aebd0418c7e96cd101262f30210ab26/plugins/vertical-plugins/equity-research/skills/model-update/SKILL.md) | 旧输入、新证据、新预测及估值差异 | “更新”指条件／证据版本，不能借此改冻结公式；复用既有ID和重放 |
| [audit-xls](https://github.com/anthropics/financial-services/blob/574ed3624aebd0418c7e96cd101262f30210ab26/plugins/vertical-plugins/financial-analysis/skills/audit-xls/SKILL.md) | 单位、公式、期间及模型一致性检查 | 优先用现有API／审阅器；原生重放与独立模型校验分开，非Excel任务不强制生成表格 |
| [ic-memo](https://github.com/anthropics/financial-services/blob/574ed3624aebd0418c7e96cd101262f30210ab26/plugins/vertical-plugins/private-equity/skills/ic-memo/SKILL.md) | 结论、依据、风险、条件与行动信息的表达 | 用于中文研究报告结构，不引入收购交易、LBO或IRR建议 |

## 按需借鉴的专题

| 固定版本源文件 | 使用场景与边界 |
| --- | --- |
| [sector-overview](https://github.com/anthropics/financial-services/blob/574ed3624aebd0418c7e96cd101262f30210ab26/plugins/vertical-plugins/equity-research/skills/sector-overview/SKILL.md) | 需要产业背景时解释需求与竞争，不扩大当前分类／多市场采集 |
| [catalyst-calendar](https://github.com/anthropics/financial-services/blob/574ed3624aebd0418c7e96cd101262f30210ab26/plugins/vertical-plugins/equity-research/skills/catalyst-calendar/SKILL.md) | 记录已知观察事件；未知日期留空，不创建自动调度 |
| [earnings-preview](https://github.com/anthropics/financial-services/blob/574ed3624aebd0418c7e96cd101262f30210ab26/plugins/vertical-plugins/equity-research/skills/earnings-preview/SKILL.md) | 财报前的研究问题清单；并非中国正式“业绩预告”的数据采集器，不替代预告契约 |
| [unit-economics](https://github.com/anthropics/financial-services/blob/574ed3624aebd0418c7e96cd101262f30210ab26/plugins/vertical-plugins/private-equity/skills/unit-economics/SKILL.md) | 单位经济适用于业务时才用，不要求所有公司计算LTV/CAC |

## 方法与执行的归属

Anthropic流程不是达摩达兰方法证明。估值定义、适用条件和原始出处沿AlphaLake[方法契约](../../docs/valuation/methodology-contract.md)与[引擎台账](../../docs/valuation/engine-change-ledger.md)；公司事实沿既有数据源，研究判断单列。未引入外部数据订阅、额外Skill依赖、新估值引擎或默认选择器。

主Skill继续使用原生计算；独立、只复用数据的建模交叉校验仍由[alphalake-dcf](../alphalake-dcf/SKILL.md)承担。改写不解除引擎冻结、不授权同步／发布，也不把行业代理、管理层计划或研究情景认证为事实或合理价格。
