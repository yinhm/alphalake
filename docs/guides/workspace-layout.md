# 工作目录与缓存约定

动态数据仅存放于项目 `workspace`（Python可用 `ALPHALAKE_WORKSPACE` 显式改根目录）；临时验证使用系统临时目录。代码、安装环境及冻结测试资料与动态数据分开，不把历史日期目录当作运行配置。

- `alphalake.duckdb`：唯一权威同步库。财务、公告身份、审核补充和已接入的参考数据在同一库内；达摩达兰原始工作簿另存 `damodaran/`，不是独立参考库。
- `tdx-cache/`：仅放 `gpcw.txt` 和直接落盘的 `gpcw*.zip`，不放对象目录、软链接或逐包JSON。每次按上游MD5和大小判断是否更新。
- `tdx/`：主库血缘引用的不可变原始证据；旧版本不覆盖，不参与日常手工缓存管理。
- `damodaran/`：按可识别的原文件名直接落盘，主库不可变证据文件追加12位内容摘要区分版本；无objects或哈希分层，行业名称映射保留在industry_lookup。
- `cninfo/`、`chinabond/` 等：按真实来源保存原文。`objects/` 按哈希寻址，必要的 `views/` 或估值文件名视图引用相同内容。
- `derived/valuation.sqlite`：网页消费的派生快照；`derived/valuation-runs/`：既有不可变估值运行；`derived/cleanup-audit/`：迁移清单、数据库审核快照及ID映射。
- `knowledge/`：词条编辑稿及 `knowledge.sqlite` 阅读库，独立于财务事实和数值参考；不随代码提交。词条 schema、只读 API 与构建命令见[知识库契约](valuation-knowledge.md)。

## 缓存行为

服务器不可用时可显式[完全离线同步财务](tdx-offline-sync.md)：`sync-financial workspace/alphalake.duckdb --offline --all`，不会等待网络重试。

财务同步先读取本地清单，再轻量检查上游清单。上游失败时可复用本地清单；来源身份刷新失败但已有证券身份时，保留并使用已有身份，不创建猜测身份。所有回退写入 `financial.local_cache_fallback` 诊断，CLI输出 `cache_fallbacks`，运行标记partial。

每个包按MD5和大小核对，优先复用已归档证据或本地缓存。上游新包下载失败，可使用主库归档哈希验证过的旧缓存；该次只处理实际缓存版本，不把新MD5写成完成检查点。清单与包版本可能不同，主库血缘绑定实际内容；不能凭文件名或修改时间认定最新。缺文件、损坏或未能验证的缓存不会变成空事实。

清单和包都通过现有上游hosts策略访问；没有HTTP后备或自建分段重组。原始文件、首次取得记录和被历史事实引用的版本保留。缓存文件先写临时文件再替换，失败不删除原始对象。

## 清理与迁移

不可变来源证据、审核记录和已引用版本保留；回滚副本在重开验收通过后清理，留下路径、哈希及原因。派生候选可重建不代表可以删除其不可重建的审核依据。禁止永久保留多个权威库、按日期重复同步目录，或向代码目录写运行产物。

迁移时的数量、路径清单和发布核验见[历史收据](../history/workspace-layout-20260925.md)；当前库版本及交付状态见[项目状态](../implementation-status.md)。
