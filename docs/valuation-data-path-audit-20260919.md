# 估值财务取值链路审计

审计基线：`a6ed011`，2026-09-19。结论：AlphaLake统一CLI已经消费标准事实及财务派生；整个估值应用尚未统一，网页仍有实际可达的SQLite/CapIQ/手工入口。三表快照和估值导出应共享标准层，无须让估值解析三表CLI输出。

本轮为静态调用链审计：核对Go查询、Python取值、FastAPI路由挂载、前端实际调用及现有回归源码；未运行全市场估值、未修改生产逻辑、未重算数据库。下列P1/P2表示整改顺序，不表示已经复现了某家公司的错误估值。

## 需要整改

### P1：网页“从数据库估值”实际读取另一套数据

[OnboardingWizard](../valuation/frontend/src/components/OnboardingWizard.tsx)第54行实际调用`valueFromDatabase`；[客户端](../valuation/frontend/src/api/client.ts)第176行请求`/api/valuation/from-database`。该路由由[main.py](../valuation/backend/api/main.py)第40行挂载，在[database.py](../valuation/backend/api/database.py)第350–368行读取`us_cn_hk_db`，直接构造`CompanyValuationInput`调用引擎。

[us_cn_hk_db.py](../valuation/backend/data_sources/us_cn_hk_db.py)第28–59行选择独立SQLite或仓库seed，不是AlphaLake DuckDB。网页上传路径也实际调用`fetch-from-file`（OnboardingWizard第109行），绕过标准物化与统一估值输入。手工创建/修改路由同样直接调用引擎，见[routes.py](../valuation/backend/api/routes.py)第1083–1118行。`fetchByTicker`客户端函数存在，但本轮未发现页面实际调用，不能与前两条同等声称在用。

影响：当前网页结果不能自动继承AlphaLake的公告时点、标准映射、缺项拒绝和运行血缘保证；不能把整个应用宣传为统一标准链路。

建议：A股网页和HTTP入口复用`company_valuation → run_batch → export-valuation → evaluate`的业务流程；服务端接收证券、报告期、截止与政策版本并获取标准数据。外部导入/手工情景若保留，须显式区分来源和验证级别，不冒称标准数据库估值，不静默作为A股主入口后备。

### P1：另一路径存在会影响估值的默认补值

[database.py](../valuation/backend/api/database.py)第120行在缺国家时回退美国；第129–132行在行业解析失败时选择行业目录第一项；第139–141行在缺国家宏观信息时补成熟市场ERP或5%、21%边际税率；第149–156行在年报日期缺失/无法解析时使用当前年份。

这些路径可继续构造输入并计算，不是经过批准的公司政策。不同问题分别可能影响WACC、税后利润和历史年份。该路由不像模板路径那样调用`_build_unresolved_fields`输出根级未解析清单；代码注释不能替代拒绝或显式政策。

建议：与入口收敛一起处理。证券、期间不得猜；行业/WACC/税率缺证须拒绝，或由显式、有版本的情景政策提供，不能默认取第一行业、美国参数或当前年份。

### P2：估值缺项原因未与三表诊断对齐

[三表宏](../internal/store/duckdb/schema.sql)第731–746行区分未审期间、同名歧义、映射有效期、源零和未物化等状态。[估值窗口读取](../valuation/backend/data_sources/alphalake.py)第312–318行则把不存在或不完整窗口归为`MissingInputs(['TDX/'+field])`；估值导出的窗口只有聚合覆盖及缺期，未带三表的详细原因。

影响：能拒绝不完整输入，但调用方难以判断应同步、物化、补审核还是提供附注，尚未把“缺失”和“未审核”完整传递到估值侧。

建议：在现有标准查询/估值输入契约中增加逐字段原因，复用统一状态判定，不在估值Python层另猜来源。不要仅将错误文字改成标准名称而缺少状态来源。

## 已符合分层的链路

| 路径 | 实际取值与结论 |
| --- | --- |
| 统一公司/批次CLI | `company_valuation.py:141`调用`export-valuation`，`run_batch`组装请求后调用`evaluate`；未见从PDF或源表直接补报表金额 |
| 增量与预测复核 | 增量复用`run_batch`；`review_valuation_forecast.py:105`从同一估值导出取得标准实际，未见改走简化源研究 |
| Go估值导出 | `valuation_export.go:240`用`fact_asof`的`f.value`，第254行用`ttm_asof`；联查`provider_fact`只取源位等校验血缘，不能因出现源表JOIN就判断绕过 |
| Python财务适配 | `standard_window_reader`读标准窗口并重算验证组成；`validated_source_value`最终返回`f.value`，源位只用于拒绝篡改，不替代标准金额 |
| 历史增长 | `alphalake.py:699`以后从标准事实中的季度收入取历史值，不读源编号或PDF |
| 附注补充 | Go导出经审核的`reviewed_supplement`；Python核对期间、单位、范围、原文与审核元数据后独立消费，不覆盖标准事实 |
| WACC、资本政策、市场桥接 | 独立参考/市场输入及显式政策，不属于三表，保持独立是正确设计 |
| 简化TDX研究 | `tools/tdx_research_source.py`是已授权研究适配，明确非标准事实；核心AlphaLake适配/API/引擎未发现导入该研究模块。冻结源证据与核验工具不列为业务绕过 |

源位校验目前与Python估值适配耦合，Go导出也平铺`source_provider_field/bits/multiplier`。这不是直接使用原始财务值，但后续可把源特定验证及独立证据整理到数据边界，保持估值只按通用字段计算；不得以解耦为由删除现有篡改拒绝。

## 建议的下一轮范围与验收

先收敛A股网页/HTTP到既有统一入口，同时取消上述猜身份/期间与隐式行业宏观默认。随后补齐估值逐字段缺项状态。不重写DCF引擎，不让每家公司新建三表解析器。

验收要求：同证券、报告期、截止、政策下CLI与HTTP消费同一标准输入并取得相同条件值；无事实、源零、未审核、过期政策各有可区分拒绝；手工/外部输入不能伪装标准链；市场与附注来源保持独立；输入、单位和既有三家公司结果的变化逐项解释。上述为下一轮门槛，本轮没有宣称已实现。
