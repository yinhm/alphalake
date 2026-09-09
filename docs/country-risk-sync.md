# 国家风险参考数据同步

当前提供 `sync-country-risk`，将达摩达兰真实 Excel 经严格解析后写入 schema 27 的参考表。公司财务链、估值参数和当前 WACC 默认值均未改变。

## 范围

数据集固定为 `damodaran/country-risk-cn-hk-us-rating-v1`：中国、香港、美国各三项（主权违约利差、国家总 ERP、国家风险溢价），加成熟市场 ERP，共 10 项。前三地使用 `rating` 方法标签，成熟市场使用 `implied_mature`。完整原始文件归档；其他国家和 CDS 列暂不标准化。不能据此宣称全市场风险数据或 WACC 已闭合。

首个审核入口固定为 [2026 年 7 月工作簿](https://pages.stern.nyu.edu/~adamodar/pc/datasets/ctrypremJuly26.xlsx)，不是动态发现最新发布的爬虫。下一季度需要先核对字段、国家范围和方法，再扩展入口。感谢 Aswath Damodaran / NYU Stern 提供数据，方法与使用说明见[原站](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datahistory.html)。

## 使用

在仓库根目录，使用已经安装 valuation 依赖的 Python 环境：

```bash
go build ./cmd/alphalake
./alphalake sync-country-risk workspace/wacc/alphalake.duckdb --python /absolute/path/to/venv/bin/python
./alphalake sync-country-risk workspace/wacc/alphalake.duckdb --python /absolute/path/to/venv/bin/python --offline
```

默认 Python 为 `python3`，需要 openpyxl。默认解析器为仓库中的 `valuation/backend/data_sources/damodaran_parsers/country_risk_parser.py`；从其他工作目录运行可用 `--parser` 指定该文件的绝对路径。该参数是本地可信代码入口，不是接受远程上传脚本的接口。没有新增 Python/Go 依赖。

命令会在目标库应用待执行迁移，原始文件存于数据库同目录的 `raw/`。输出 JSON 包含 run_id、artifact_id、release_id、inserted、observations。`inserted=false` 表示这一解释版本已存在且逐行一致，不代表本次未下载；只有 `--offline` 保证不发 HTTP。

离线模式选择本地最近发布的该数据集快照所引用的归档，并再次核验字节哈希。解析失败的较新下载不会取代它。这里的“最近发布”只是重放入口，不是按经济时点自动选择估值输入；尚未提供参考数据 ASOF 或自动 WACC 查询。首次同步失败且没有已发布快照时，离线模式明确失败。

## 发布保证

1. 先建立持久化运行；HTTP 成功响应完整归档后解析，空响应和超过 16 MiB 的响应拒绝。Excel 解压总量限制 128 MiB。
2. 复用现有 Python 国家风险解析，追加严格表头、国家唯一性、日期、必需数值与范围校验。CN/HK ERP 分量关系校验不套用美国行。
3. Go 校验 10 项完整范围、唯一性、原值与 12 位标准值的舍入误差，核对归档哈希、来源、数据集、URL及运行状态。CLI 不接受手写标准 JSON 绕过原始解析。
4. 内容签名固定原文件哈希、解析器版本、实际执行的 Python 脚本哈希、Python/openpyxl 版本及标准化规则。相同解释必须与已发布记录、血缘和检查点一致；不一致即报错，不能覆盖。
5. release、归档关联、10 项观测和内容级检查点在同一事务提交。失败事务不留下部分发布；归档、失败运行及诊断保留。运行终态单独持久化；如果终态写入失败而发布已提交，重试通过内容级幂等协调，不宣称两个事务是同一原子操作。
6. 不自动推断 supersedes，不使用下载顺序替代经济时点，不删除旧版本。不同解析环境会产生不同解释版本，旧版本可保留。

公开可用时间当前统一使用该内容首次归档时间，`availability_basis=first_seen`；Excel 基准日只是 observation_date。7 月文件在 9 月首次抓取，不会伪装成 7 月已经可用的历史输入。

## 查阅

```sql
SELECT r.release_id, r.available_at, r.availability_basis,
       o.subject_code, o.metric_code, o.method_code, o.value,
       o.raw_value, o.source_locator, a.sha256
FROM meta.dataset_release r
JOIN reference.country_risk o ON o.release_id = r.release_id
JOIN meta.artifact a ON a.artifact_id = o.artifact_id
WHERE r.release_id = 1
ORDER BY o.subject_code, o.metric_code;
```

替换为命令实际返回的 release_id；显式查询固定版本，不将该 SQL 当作自动历史选择策略。

## 验收与下一步

[真实归档及验证说明](../internal/source/damodaran/testdata/README.md)记录来源和哈希。Go 存储测试覆盖缺失归档、终态运行拒绝、事务中途唯一键冲突回滚与幂等重放；Python 从 XLSX 原始 XML 独立核对 10 项，并反向验证表头改名、必需值缺失、ERP 篡改及国家重复；Go 本地 HTTP 集成验证失败保护与重开持久化。CI 显式运行跨语言集成验收，不依赖本地 workspace 留存。

下一步是行业 Beta 与人民币收益率的真实来源接入，再建立显式时点和方法选择。FX、industry_stat、yield_curve_point 当前仍只有表结构；本轮的发布函数限定国家风险，未引入多数据类型的通用发布接口。SQL 管理员仍可直接修改表，可信发布保证适用于该生产写入路径。

## 2026-09-09 执行记录

- 实际联网命令在隔离库 `workspace/wacc-reference-20260909/acceptance.duckdb` 返回 `run_id=1, artifact_id=1, release_id=1, inserted=true, observations=10`。
- 同一库执行 `--offline` 返回 `run_id=2, artifact_id=1, release_id=1, inserted=false, observations=10`。
- 全套 Go 测试在指定真实 Python 解释器下通过，跨语言集成测试未跳过；构建、vet 通过。
- Python 后端 145 passed / 4 skipped；4 项为既有外部条件测试，本轮新增 5 项全部执行。openpyxl 的 Excel 数据验证扩展提示不影响只读数值提取；原文件不经保存改写。
- workspace 验收库不提交；完整原始测试归档、固定预期、独立 XML 校验和 CI 步骤已入库。既有财务生产库未修改。
