# 安克现金历史主库发布

本页记录当时将主库`workspace/auto-valuation-20260909/market.duckdb`发布为schema38；当前已完成后续[历史损益schema39发布](earnings-main-publication-20260911.md)。该次现金发布的标准事实从2,023,155增至2,023,185。统一入口的安克2026H1现金检查从历史缺项转为可用，原通用政策run ID及92.1504元/股条件值不变。信息截止仍为2026-09-10T22:18:56.906406Z，不是新的当前目标价。完整[发布、重开及估值回执](acceptance/cash-main-publication-20260911.json)留档。

## 发布范围与保护

来源仍为TDX，新增三期整条裁剪记录中的27条既有映射事实，以及迁移038获准的3条FN114资本开支事实，全部属于安克2024H1、Q3、FY。七个源值已逐项核对原文；新增30条事实不等于本轮独立原文审核了30个值，更不代表全市场历史补齐。

发布前保留原库硬链接备份`market.duckdb.pre-cash-history-20260911`，随后复制已验收副本、核对SHA256并原子替换主库路径。原备份SHA256仍为`84dc65a8a326c7aa75376097e78421c955597d132e52b8625d00a0e4177b70c4`。替换时持有数据库只读连接，原库和候选受到原生写锁保护；新库先以临时文件打开核验，再更名发布。主库路径随后指向新的独立文件，重放写入不会修改备份。

新增1份公告目录、3份PDF和3份裁剪包，先按哈希复制到主库对应`raw/`根目录；已有同路径文件若哈希不同则拒绝，不覆盖证据。主库重开后再次逐份校验，避免数据库已发布而原始文件只存在于副本目录。

精确比较结果：原标准事实全列（包括运行编号/写入时间）、原安克源事实、原归档及运行记录没有变化；证券身份、分类成员、reference参考表、补充输入、检查点及非FN114映射均无变化。FN114除有效期和说明以外的列保持一致。

另有3,664条非目标公告因身份重试刷新`ingest_run_id`和`last_seen_at`。首次全列比较发现该差异后，单独核对其余全部字段无变化；身份、披露时点、首次取得、原文引用及解析状态没有变化。这是运行元数据刷新，不称新公告或财务值变化。发布回执明确保留该计数。

## 重开验收

运行5366物化重放新增/更新/删除均为0，标准事实仍为2,023,185。既有582,365项映射候选拒绝继续保留，不等于582,365家公司失败；本次不是新全市场同步或估值批次。重开后的主库与原备份再次执行相同内容比较，结果全部一致，新增归档均在主库根目录可读且哈希匹配。

统一入口安克run ID仍为`c03a391c27c7d803d09d6fe166daa9b2bef8b9227a6c79cc7c931f11bb6a653c`。完整运行文件与副本验收文件逐字节相同，估值输出仅`evidence.run_file`位置变化。现金预测OCF 16.4115亿元、资本开支3.1963亿元、现金代理13.2151亿元，均未变化；与DCF首年FCFF的差额仍未完全分类，不据此调整估值。

本地5,569家集合的就绪度摘要哈希与验收副本相同。此前5,304家核心齐全、2,461家条件估值是[9月10日批次](a-share-automation-acceptance-20260910.md)的验收数字；本次不把它们冒称新一轮全市场估值结果。

## 使用与复核

后端Python环境执行，政策显式指定：

```bash
cd valuation/backend
python -m tools.company_valuation ../../workspace/auto-valuation-20260909/market.duckdb 300866 \
  --period 2026-06-30 --as-of 2026-09-10T22:18:56.906406Z \
  --policy ../examples/nonfinancial-baseline-2026H1-pilot.json \
  --alphalake ../../alphalake --cash-check
```

专项只读复核工具位于`cmd/check-cash-publication`，以原备份为基线可重复检查已发布库：

```bash
ALPHALAKE_DUCKDB_MEMORY_LIMIT=1536MiB ALPHALAKE_DUCKDB_THREADS=2 \
go run ./cmd/check-cash-publication \
  workspace/auto-valuation-20260909/market.duckdb.pre-cash-history-20260911 \
  workspace/auto-valuation-20260909/market.duckdb \
  workspace/anker-cash-history-upgrade-20260911-v2/acceptance.json
```

该工具默认模式固定本次schema37→38及30条事实边界；新增`--earnings`仅用于后续历史损益发布。默认模式依赖已验收副本与其回执，不是通用数据库发布器，也不替代原文/源位验收。默认只读；`--publish`用于本次实际发布，拒绝已存在的备份/临时文件，主库发布后不重复使用该选项。基线变化、证据损坏或非预期内容变化均拒绝。

Go全套测试、构建、vet通过；新增回归覆盖基线变化时拒绝发布、已有副本不覆盖及坏哈希复制拒绝。实际主库比较、备份、原子发布、重开、归档和统一Python CLI验收通过；本轮未改Python代码或依赖，不重复跑Python全套。历史原包为后来取得的版本，严格PIT和完整FCFF有效性边界不变。
