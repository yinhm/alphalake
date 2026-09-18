# 两家公司已审核资产发布（2026-09-18）

安克、苏泊尔2026H1已审核资产已从样本验收接入正式财务库；范围固定为两家公司、五项补充、两份原文。TDX标准金额不变，PDF只用于身份、分类及源精度核验；不扩大到全市场，不新增公司资本倍率批准。

## 正式数据与采用政策

- 正式库：`workspace/auto-valuation-20260909/market.duckdb`，schema44；备份：同路径加 `.pre-reviewed-assets-20260918`。
- 原始归档：`workspace/auto-valuation-20260909/raw`。发布前新增三份不可变文件：安克CNINFO原件、苏泊尔新浪镜像原件、镜像审核JSON；来源分开留痕。
- 安克下载回执复用 `internal/ingest/testdata/anker-valuation-2026/reports.json`，原始取得时间2026-09-06。补充是第108页联营投资总额556,090,434.30元，股权桥接仍消费TDX标准数556,090,432元。
- 苏泊尔复用[独立镜像审核及四项补充](../valuation/research/reviewed-assets-20260917/supor/README.md)。金融资产标准总额扣除7.7亿元受限分量后加回；不恢复float32损失的小数，不重复流动／非流动分量。
- [专用政策](../valuation/examples/batch-reviewed-main-20260918.json)将源包、补充导入哈希及导出证据内容全部绑定到主库。安克原隔离测试使用裁剪包，主库使用全量包，因此原政策不能直接照搬；本次只改真实证据绑定。
- 原补充格式无精确审核时间字段，历史表保留NULL；本次系统入库时间单独记录。采用政策仍保存显式审核日期和有效期，不将旧下载日冒充本次入库日。

财务／股本基期均为2026-06-30，查询信息截止2026-09-18T00:00:00Z；审核及系统入库时间另行保留，不声称严格历史系统时点回放。安克政策有效至2026-10-17T00:00:00Z，苏泊尔至2026-10-17T16:50:00Z；到期拒绝，不自动延期。WACC分别固定旧基线8.0753671970%、5.7531403615%，**不是9月18日市场WACC或当前目标价**。账面回收率1仍是已披露代理假设。

| 公司 | 未采用新增资产 | 采用后，元/股 | 正式运行ID |
| --- | ---: | ---: | --- |
| 安克创新300866 | 126.1378483323 | 127.1544634851 | `6cff68b89a414ea23ebff6e2a860caf318359a46cd6b91a255938af6df2428bb` |
| 苏泊尔002032 | 43.2526126801 | 45.7299290863 | `779c868187a3641528ff50f68c892b7171e8f0f531edbe4ee641f84c910123f9` |

## 验收与发布

[发布回执](acceptance/reviewed-main-publication-20260918.json)记录库文件SHA-256、备份、逐表内容摘要及估值验收。全量源事实、标准事实、映射及其他既有表内容不变；只允许两份公告增加文档关联、三条归档、两条文档历史、一条镜像审核、五条有效补充及五条首次审核历史。全部新归档重新核对文件哈希。逐表摘要为行数及全列哈希XOR／和，不冒称逐行密码学相等证明。

先副本验收、锁住原库及候选的原生DuckDB连接并校验双方文件哈希，再保留原库硬链接备份、原子替换、目录同步。发布后重开正式库，经真实统一CLI再次得到相同run ID与完整输出。WACC参考库未变更。

- 769条两公司事实数值和各80个窗口逐项相同；全部收入、EBIT、再投资、FCFF及经营价值不变；独立Decimal复算通过。
- 原文和补充重放均零新增；增量估值在证据变化时重算、未变时复用。缺证、内容哈希不符、过期分别拒绝。
- 临时真实副本逐公司撤销一项：拒绝估值，旧发布重放不复活；显式恢复生成新审核头，旧政策仍拒绝，重新绑定后数值恢复。每项保留发布／撤销／恢复三动作；临时副本删除，生产库不写入试验撤销历史。
- Go全套测试、构建及相关vet通过。Python方法、增量重估和真实集成95项通过，无skip，2条既有依赖弃用warning；苏泊尔5个PDF金额、2个源位与5路篡改拒绝另行通过，安克PDF语义与金额由真实集成回归核验。

## 使用与复验

```bash
PYTHONPATH=valuation/backend \
ALPHALAKE_VALUATION_RUN_DIR=workspace/reviewed-main-20260918/runs \
workspace/anker-agent-adapter-20260906/venv/bin/python -m tools.company_valuation \
 workspace/auto-valuation-20260909/market.duckdb 300866 \
 --period 2026-06-30 --as-of 2026-09-18T00:00:00Z \
 --policy valuation/examples/batch-reviewed-main-20260918.json --alphalake ./alphalake
```

苏泊尔将代码换为`002032`。该专用政策须显式提供；默认行业政策不改，不自动给未经审核的公司采用资产加回。

```bash
PYTHONPATH=valuation/backend \
workspace/anker-agent-adapter-20260906/venv/bin/python -m tools.verify_reviewed_main \
 --lifecycle \
 workspace/auto-valuation-20260909/market.duckdb.pre-reviewed-assets-20260918 \
 workspace/auto-valuation-20260909/market.duckdb \
 workspace/reviewed-main-recheck
```

复验工具复用已有适配器、共享引擎和增量入口。`--lifecycle`临时复制整库，需约2GB空间，退出后删除；仅核对计算与绑定可省略该选项。解释器沿用现有venv，需现有backend依赖与pypdf；原件语义测试在CI，主库发布操作不在CI自动执行。

剩余主线是公司经营预测与资本需求的经济依据。安克公司资本倍率仍未批准，继续披露行业代理；本轮解决证据的正式采用与失效传播，不声称DCF预测准确性已经提升。
