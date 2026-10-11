# Linux／Python 的 DCF Excel 交付

需要生成或检查Excel时读取本页。当前完整导出器是 `valuation/backend/api/export_workbook.py` 的 `generate_full_workbook`，API是 `GET /api/valuation/{session_id}/export/full-workbook`。模型DCF、融资及股权结果是共享响应的数值快照；辅助页的公式和交叉引用不驱动DCF。

## 复用原生导出

1. 从本次已经核验的完整 `{"inputs": ...}` 请求建立研究临时会话，不重选数据源或继承另一家公司会话。
2. 核对返回的 `inputs/dcf/final/reference_snapshot` 与归档条件一致后下载完整工作簿。线上API需核对发布快照；未提供网页地址时，按当前条件工具的既有方式使用本地 `TestClient`，先将 `US_CN_HK_DB_PATH` 指向已确认SQLite。
3. 下载后校验XLSX可由 `openpyxl.load_workbook` 打开，关键结果单元格与完整响应一致，再删除本次临时会话。失败也释放会话，不能删除用户已有会话。

线上导出示例从仓库根目录用 `PYTHONPATH=valuation/backend .venv/bin/python` 执行。`ALPHALAKE_WEB_URL` 来自已确认配置；请求、归档响应和目标路径在 `workspace/derived`，导出到新文件。大任务沿项目隔离约定运行。

```python
import json
import math
import os
from pathlib import Path
from urllib.request import Request, urlopen
from engine.data_dictionary import CompanyValuationInput

base = os.environ["ALPHALAKE_WEB_URL"].rstrip("/")
request = json.loads(Path(os.environ["ALPHALAKE_REQUEST"]).read_text())
expected = json.loads(Path(os.environ["ALPHALAKE_RESPONSE"]).read_text())
target = Path(os.environ["ALPHALAKE_XLSX"])
session_id = None
try:
    req = Request(base + "/api/valuation", json.dumps(request, allow_nan=False).encode(),
                  {"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=60) as response:
        actual = json.load(response)
    session_id = actual["id"]
    assert (CompanyValuationInput.model_validate(actual["inputs"]) ==
            CompanyValuationInput.model_validate(expected["inputs"])), "本次API与归档不一致：inputs"
    for key in ("dcf", "final", "reference_snapshot"):
        assert actual[key] == expected[key], f"本次API与归档不一致：{key}"
    with urlopen(base + f"/api/valuation/{session_id}/export/full-workbook", timeout=60) as response:
        content = response.read()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as output:
        output.write(content)

    import openpyxl
    wb = openpyxl.load_workbook(target, data_only=False)
    try:
        per_share = actual["final"]["value_per_share"]
        cell = wb["Valuation Output"]["B27"].value
        if per_share is None:
            assert cell is None, "缺每股值不得写0"
        else:
            assert math.isclose(cell, per_share, rel_tol=1e-12, abs_tol=1e-8)
        assert math.isclose(wb["Cost of Capital"]["B28"].value,
                            actual["cost_of_capital"]["wacc"], rel_tol=1e-12, abs_tol=1e-12)
    finally:
        wb.close()
finally:
    if session_id is not None:
        with urlopen(Request(base + f"/api/valuation/{session_id}", method="DELETE"), timeout=60) as response:
            assert response.status == 204, "临时导出会话未释放"
```

示例单元格来自当前导出器，使用前核对实际代码；金额、百分比与每股数值不能只比较显示后四舍五入的字符串。输入按既有typed模型比较：同一时点的 `Z` 与 `+00:00` 写法不同，不是数据变化；真实时点差异仍拒绝。无每股值的经营价值模型保留空值，不能写0。不得把 `.xlsx` 文件已存在或ZIP可解包视为数值核验完成。

## 中文交付副本

原生导出保留原页布局。在新的交付副本按需要添加以下页，不覆盖原归档或原始导出：

| 页面 | 内容 |
|---|---|
| 估值摘要 | 公司、条件名、财务和报价时点、经营价值／股权／每股结果、终值占比、主要限制与运行引用 |
| 来源与假设 | 标准字段、数值、单位、期间、JSON字段路径、来源定位／哈希、参考版本、条件依据和用户覆盖 |
| 敏感性 | 明确轴、中心条件、每格条件值或拒绝、完整请求及结果定位；数值由共享API重算 |

标题和说明以中文为主，保留准确的标准字段与原生工作表名称用于追溯。使用已有 `openpyxl`；先固定行列位置，再添加内容，不在写公式后插行破坏引用。保持冻结窗格、数字格式、合理列宽和打印范围，避免整页彩色底纹。

原生配色有既定含义，保持一致：蓝色为来源财务，绿色为显式条件，灰色为计算快照，橙色为参考。新增硬编码来源值和条件值添加批注：`来源／条件类型；实际日期与期间；文件或API定位；SHA-256或参考ID；假设依据`。未知日期写未知，研究判断不得伪造报告出处；结果单元格注明响应字段和运行引用。

## 敏感性核验

- 5×5为常用展示形式，不要求每次生成三张75格表。选本次真正生效的两项驱动，先保存轴、条件中心和扰动理由，再计算；完整年度或融资路径须按既有字段整体变更，不只改失效标量。
- 各格请求除已声明路径及必要的一致性联动外保持同版输入。保存这些变化；中心请求等于已选原条件，完整 `dcf/final` 须复现，不能只对齐显示到分的价格。
- 用共享引擎／原生API计算，先小批核验，再串行完成网格；不得并发启动每格进程或重导财务库。价格、股本、事实、源参考快照不因网格而刷新。
- 拒绝格展示具体原因。需要保持 `g < WACC`、正终值ROIC及原生输入界限；不能用 `IFERROR(...,0)` 隐藏无效组合。
- 低WACC／高增长等轴的方向只是诊断，不强制结果单调：税盾、资本需求及其他联动可能改变结果。发现异常先追踪实际输入和现金流，不把矩阵当概率或合理价格区间。

## 可编辑公式的能力边界

当前DCF导出是**结果快照**。编辑预测需要在AlphaLake网页／API重算，再重新导出；Skill不新增独立Excel估值引擎，也不把静态敏感性格冒充自动联动公式。

若用户明确要求“在Excel直接改假设，全部DCF自动重算”，先说明现有导出不满足，记录具体方案及所需方法／引擎边界并按项目约定审批。不得临时以简化FCFF、终值或税务公式做出看似可编辑但结果不一致的模型。

`openpyxl` 能写公式，不能执行公式。只有使用实际可用的Linux公式计算工具并检查重算结果后，才可声称公式已重算；没有LibreOffice、`xlsx` skill或`recalc.py`不阻止原生结果快照交付。不要写不存在的工具路径，不自动安装Office依赖，不把 `data_only=True` 读到的空缓存当成公式成功。需要公式核验时独立报告工具缺项，并保留已核验的原生数值结果。
