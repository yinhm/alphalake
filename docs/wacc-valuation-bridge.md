# 固定参考版本到 WACC／估值桥接

已支持安克 300866（合并口径）和茅台 600519（酒类政策代理口径）的 2026H1 财务链，通过显式参考版本与政策计算 WACC，再进入共享 M1–M6。原直接输入 WACC 路径保持兼容；没有新增迁移或依赖，没有把参考输入或预测写入公司标准事实。

## 操作

先按[国家风险](country-risk-sync.md)和[行业 Beta／国债曲线](beta-yield-sync.md)操作，把三类数据同步到同一个参考库。记录每次同步返回的 release_id；它是全库共享编号，不能假设每类都从 1 开始。

```bash
./alphalake export-wacc-references workspace/wacc/alphalake.duckdb \
  --as-of 2026-09-09T12:03:36Z \
  --country-release 3 --beta-release 1 --yield-release 2 > workspace/wacc/references.json
```

示例编号和时间必须换成自己库中已取得的版本及实际截止时间。导出不推断“最新”，校验来源／数据集、归档关联、内容检查点及完整范围（10／376／8 项）。`available_at <= as-of`；可加 `--recorded-cutoff RFC3339` 限制系统当时已入库的版本。未提供 recorded-cutoff 表示使用当前库知识，不能声称重现过去系统状态。首次取得时间不会倒推成年初公开时间。

启动 valuation 后端后，在 `valuation/backend` 执行：

```bash
python -m tools.valuate_alphalake /absolute/path/to/financial.duckdb 300866 \
  --period 2026-06-30 --as-of 2026-09-09T12:03:36Z \
  --policy ../examples/wacc/anker-forecast-policy.json \
  --wacc-policy ../examples/wacc/anker-target-policy.json \
  --wacc-references /absolute/path/to/references.json
```

两个导出须使用完全相同的信息截止时点。茅台换证券代码及两个 `moutai-*.json` 文件。原接口 `POST /api/valuation/from-alphalake` 新增可选 `wacc_binding={references,policy}`；与经营政策里的直接 `wacc` 二选一。CLI 两个 WACC 文件参数必须成对提供。

## 事实和政策

参考包保留原值、标准小数、观察日期、方法、来源定位、发布版本、归档 SHA、解析／标准化版本、首次可用和入库时间。接口验证结构血缘、原值换算、完整条数、选中项唯一性、单位、方法、样本数及政策允许的陈旧天数；不是数据库数字签名，不证明客户端上传的包未经重写。需从可信数据库导出并保存请求。

公司行业归属、行业权重、国家风险暴露权重及 lambda、期限、是否扣主权违约利差、目标债务权重、税盾率、税前借款成本都来自附理由的政策。行业 D/E／有效税率没有自动替代公司目标结构／边际税率。权重须各自合计为 1；国家风险贡献允许通过 exposure_scale 单独缩放。

```text
RF = 选中人民币国债到期收益率 - 政策选择的中国主权违约利差（或不扣）
BetaU = Σ 行业权重 × 选中行业去杠杆 Beta
D/E = 目标债务权重 / (1 - 目标债务权重)
BetaL = BetaU × [1 + (1 - 税盾率) × D/E]
国家风险贡献 = Σ 国家权重 × exposure_scale × CRP
Ke = RF + BetaL × 成熟市场 ERP + 国家风险贡献
WACC = (1 - 目标债务权重) × Ke + 目标债务权重 × 税前借款成本 × (1 - 税盾率)
```

新增 `reference_snapshot` M2 分支，避免改变原引擎将整个 ERP 乘 Beta 的既有行为。结果保存分量、政策、选中原观测和边界，未知股债市场金额为 null。目标债务权重为零不等于公司没有租赁或债务。首批终值政策是维持当前 WACC，须显式接受；WACC 不大于终值增长率时拒绝。

## 验证与实际记录

自动回归使用归档参考文件对应的固定解析结果走 Go 生产发布／导出，再与真实 TDX 标准财务导出合并，调用 HTTP 测试接口；来源解析另由已有 CI 实测。两家公司用 Decimal 从导出分量独立复算 WACC，并与相同折现率的旧直接分支比较最终每股值。覆盖未来可用／入库时间、陈旧、重复、缺项、错误口径／单位／方法／权重等拒绝条件，失败不保存成功记录。金融导出保留小数秒，避免与参考导出截止时点失配。

2026-09-09 隔离验收：`workspace/wacc-beta-yield-20260909/acceptance.duckdb` 补同步国家风险，run 5／artifact 3／release 3，新增 10 项；行业和曲线分别固定 release 1／2。财务库为 `workspace/valuation-integration-20260907/exports/acceptance.duckdb`。真实 CLI 导出→本地 uvicorn HTTP→共享引擎→持久化运行均成功，结果留在 `workspace/wacc-bridge-20260909/`。

| 桥接示例 | WACC | 每股模型输出（元） |
| --- | --- | --- |
| 安克 | 6.972058% | 156.30 |
| 茅台 | 5.038113% | 2376.01 |

这两组数字**仅为桥接验收**：经营预测复制原 central 示例，不是此前调研后的修订预测。安克采用单一消费／办公电子行业、全中国风险暴露、5% 目标债务权重、3% 借款成本、20% 税盾；茅台采用酒类行业、全中国风险暴露、0% 目标债务权重、25% 税盾。两者扣中国违约利差，均固定终值 WACC。不能将模型输出称为新目标价或市场估计，低折现率会明显放大终值。

尚未闭合：真实业务／地域暴露映射、同日多类别股本与市值、债务市场价值／信用利差、市场校准的资本结构和终值风险假设。汇率及其他币种也未接入。历史 FCFF 继续缺失，不因 WACC 可计算而补零。下一步应补这些市场资本结构与公司暴露证据，并与修订经营预测共同审查。
