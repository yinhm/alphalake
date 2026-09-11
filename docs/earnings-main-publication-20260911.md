# 安克历史损益与一季度收入主库发布

主库`workspace/auto-valuation-20260909/market.duckdb`已发布为schema39，标准事实2,023,185→2,023,212。新增27条仅属于安克：2024H1/9M/FY六个损益字段18条，以及2024Q1九条既有映射事实。并非全市场历史补齐，也不等于本次独立原文核验27个金额。

沿用[已冻结的2025H1政策](../valuation/research/standard-history-2025H1/policy.json)，统一公司入口现在可直接从主库生成历史条件估值。完整请求及报告与原副本归档一致，58.8184元/股不变；主库到期核验的完整10个位置亦一致，1个到期、9个未来保留；新run ID为`bc882d3e2437e5422ae8a5182ce84ea69a615496554cad23963a07acf26e3359`，变动来自上轮历史税项修正后的引擎版本，而非财务或预测参数变化。它已保存在默认运行目录，可由历史运行查询发现。

财务基期2025-06-30，信息截止2025-09-01中国零点，股本仍为财报基期；这是2026年事后创建的示例运行，不是当年已留存预测或当前目标价。原始版本后来取得，严格PIT及完整FCFF的既有限制不变。

## 发布与核验

[完整回执](acceptance/earnings-main-publication-20260911.json)保留发布前比较、发布、重开比较、物化重放及统一入口核验。

原库硬链接备份为`market.duckdb.pre-earnings-history-20260911`，SHA256仍为`f25e30ba9dc73481a29ac0c385641d34834fcc73428070bfb742c245d6deb4ec`。候选库在发布前比较后锁定SHA256，发布前再次核对；先复制为独立临时文件，再原子替换主库路径，后续写入不影响备份。取得原生只读锁后再次核对基线哈希，避免核验期间基线已变。

原标准事实全列、原安克源事实、原归档和运行记录均未改写；证券身份、分类成员、参考表、补充输入和检查点未变化。六个目标映射除有效期/说明外的列不变。3,664条非目标公告仅刷新运行编号与最后观测时间，其余内容不变。新增标准金额、证券/期间/来源、字段/单位及源版本均与对应源事实和映射一致。

新增目录、2024Q1 PDF及整条TDX裁剪包三份证据，按哈希复制到主库`raw/`，同路径异内容会拒绝覆盖；重开比较再次逐份校验。运行5376物化重放新增/更新/删除均为0；582,365项既有映射候选拒绝保留，不是公司失败数。重放会增加运行/诊断记录，发布后的文件哈希变化不等于财务变化。

复用`cmd/check-cash-publication`，新增`--earnings`仅支持这次固定schema38→39、27条事实范围，不扩展成任意迁移发布器。无此选项时保留旧现金发布范围。发布必须传入带候选SHA的预检查回执；日常复核只读即可：

```bash
ALPHALAKE_DUCKDB_MEMORY_LIMIT=1GB ALPHALAKE_DUCKDB_THREADS=2 \
go run ./cmd/check-cash-publication \
 workspace/auto-valuation-20260909/market.duckdb.pre-earnings-history-20260911 \
 workspace/auto-valuation-20260909/market.duckdb \
 workspace/earnings-history-upgrade-20260911/acceptance.json --earnings
```

不要对已发布主库重跑`--publish`；备份/临时目标已存在时拒绝覆盖。只读模式沿用状态名`prepublication_checks_passed`，回执中的`postpublication`明确表示这次比较发生在主库重开之后。

## 日常调用

下面只包装既有政策，不复制维护第二套参数；生成的文件是CLI配置，不是新工具实现：

```bash
python3 - <<'PY'
import json
from pathlib import Path
root=Path('workspace/earnings-main-publication-20260911')
root.mkdir(exist_ok=True)
p=json.loads(Path('valuation/research/standard-history-2025H1/policy.json').read_bytes())
config=dict(policy_version='anker-standard-history-2025H1-v1',review_note='既有冻结政策的统一入口包装；事后链路验收，不改任何预测参数。',assignments={'300866':{'policy':p}})
(root/'policy.json').write_text(json.dumps(config,ensure_ascii=False,indent=2))
PY
PYTHONPATH=valuation/backend python -m tools.company_valuation \
 workspace/auto-valuation-20260909/market.duckdb 300866 \
 --period 2025-06-30 --as-of 2025-09-01T00:00:00+08:00 \
 --policy workspace/earnings-main-publication-20260911/policy.json
PYTHONPATH=valuation/backend python -m tools.review_valuation_forecast \
 workspace/auto-valuation-20260909/market.duckdb \
 bc882d3e2437e5422ae8a5182ce84ea69a615496554cad23963a07acf26e3359 \
 --as-of 2026-09-10T00:00:00+00:00
```

Python命令使用已安装项目后端依赖的环境；引擎再变动会产生新的run ID，以当次公司入口结果为准。Go全套、构建及专项vet通过；回归覆盖金额、身份、单位、源引用/来源篡改拒绝，以及无候选哈希拒绝发布。Python代码及依赖未改，不重复跑后端全套，实际统一入口和历史核验单独执行。

发布及重开验收后，已按原哈希确认并删除被替代的`workspace/earnings-history-upgrade-20260911/acceptance.duckdb`副本，释放约1.87 GiB；该目录的回执、导出和原始证据保留，主库及原库备份保留。旧副本路径不能再用于直接重开，历史数值可由主库及仓库压缩归档核验。
