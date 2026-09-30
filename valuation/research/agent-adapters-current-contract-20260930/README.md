# 双公司适配器现行契约证据

本目录为2026-09-30显式重建的安克/茅台CI预期。原`internal/ingest/testdata/*-agent-adapter-2026/{request,requests,result}.json`保留冻结，不覆写。这里只更新已存在的现行契约与来源血缘；引擎、公式、假设、财务输入、十年预测、缺项、警告、桥接及条件每股值均未改。

## 已审核差异

- 原安克脚本的M2直接调用补传当前必需的`book_debt=raw.bv_debt`，与共享编排器一致；其值另外核对，不放宽接口、不补新事实。
- 现行模型新增的空字段：合并账面权益、年度资本倍率、两个行业调整后利润率，以及空年度研发字典。明确列出每项默认值后，完整Pydantic往返必须逐字段一致，不能自动接受未知字段或未来默认变更。
- `facts.csv`/`windows.csv`哈希变化来自已有提交`586677c2ecb0ddb886fe82dc64490e6a5f7159c2`：新增20个已审核投资科目源零，3个窗口由缺失变为明确零。重建程序精确移除这20行、还原3个窗口，并复原冻结证据引用的旧SHA-256；原有事实行变更/删除为0。其他CSV来源哈希逐项不变。
- 四份JSON只作上述精确转换，所有旧数值和其余内容保留。默认适配脚本继续独立复算两公司四情景，并与本目录完整JSON逐字核对；不是仅筛选几个结果字段比较。安克101.64374605804052元，茅台733.7874568503961、1269.019864567976、1888.8165157077221元，均为原条件组合模型，非当前市场目标价。

## 复现

使用项目Python依赖，显式写入**不存在的新目录**：

```sh
PYTHONPATH=valuation/backend python -m tools.rebuild_agent_adapter_evidence /tmp/adapter-contract-review --recorded-at 2026-09-30T10:00:00+00:00
```

`--recorded-at`记录实际重建时点；上例是本次收据时点，未来重建应使用当时时间。重建工具不执行估值、不自动更新期望数值、不提供应用运行时兼容。收据保存四份冻结输入/新预期哈希、来源变更及全部契约新增项。内容变化须另行审核。

默认完整验证仍执行Go生产标准链、PDF/补充审核，再执行实际Pydantic、共享引擎、逐年独立复算、缺项和茅台期限反例：

```sh
python internal/ingest/testdata/anker-agent-adapter-2026/verify.py
python internal/ingest/testdata/moutai-agent-adapter-2026/verify.py
```

以上含Go的验证须按仓库资源隔离要求运行。轻量回归`valuation/backend/tests/test_agent_adapter_evidence.py`仅在测试中隔离这一步源链子进程，完整执行两脚本其余计算；CI默认脚本没有跳过开关。回归另覆盖全部重建字节、原冻结文件不变、已有输出目录拒绝、数值/缺项/契约篡改、源哈希篡改及上游失败中止。轻量通过不冒充再次运行实链或HTTP验收。
