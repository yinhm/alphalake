# 估值知识库：代码与内容契约

本功能为原生估值页面增加词条阅读能力。代码随仓库交付，词条数据库和编辑稿保存在 workspace；首版真实词条内容另行编写、核验，不随本次代码提交。词条服务不依赖估值会话，不参与估值计算。

## 阅读入口

- `/knowledge`：搜索及浏览词条，未创建估值也可访问。
- `/knowledge/<term_id>`：可分享的独立词条页。
- 表格标签旁的帮助按钮：打开该标签对应词条，支持键盘和触屏。
- 开启“划词解释”后，选取阅读区词汇显示简释，再展开完整解释。已标注标签支持选中部分词汇；普通文字使用中英文标题及别名匹配，有歧义时由用户选择。

视口宽度达到 1600px 时，384px 知识栏与估值主栏并列；更窄时使用可关闭的模态抽屉。导航可收起；知识栏独立滚动。划词弹层不自动夺取焦点，输入框、可编辑单元格及跨单元格选择不触发解释，复制操作保持浏览器行为。划词开关在当前浏览器保存。

词条正文、AlphaLake 页面用途和当前估值数值分别展示。当前数值仅由前端 `knowledge/liveValues.ts` 的明确白名单从 `ValuationResponse` 读取；数据库不能执行表达式或读取任意字段，缺值不补零。页面绑定可包含使用限制和所核验的引擎版本。正文支持受限 Markdown 和数学公式，禁用原始 HTML，不加载正文远程图片。

稳定的页面绑定契约由 `knowledge/liveValues.ts` 中的 `KNOWLEDGE_BINDINGS` 导出。内容准备时须同时匹配 `binding_id`、`term_id` 和 `field_path`；例如 `wacc.current` 绑定 `wacc` 与 `cost_of_capital.wacc`，`wacc.stable-override` 绑定稳定期覆盖输入，不能互换。多年度表格中仅标注概念，不把某一基年值当成所有列的共同值。

## 存储边界

默认位置：`workspace/knowledge/knowledge.sqlite`。`ALPHALAKE_WORKSPACE` 可改变 workspace 根目录，`ALPHALAKE_KNOWLEDGE_DB` 可显式指定知识库文件。该 SQLite 是编辑性知识内容，与主 DuckDB 中的财务事实、参考数据及 `derived/valuation.sqlite` 财务发布快照职责不同，不新增财务权威来源。

| 表 | 职责 |
|---|---|
| `kb_term` | 稳定 `term_id`、中英文名称、分类、简释、正文、内容哈希 |
| `kb_alias` | 别名、语言、归一化名称；同一别名可对应多个词条 |
| `kb_relation` | 相关、前置等词条关系及排序 |
| `kb_source` | 原始出处、作者、URL、发布日期与核验日期 |
| `kb_citation` | 词条与出处关系、章节及页码等定位 |
| `kb_binding` | 页面/字段用途、上下文、限制及核验版本 |
| `kb_release` | schema 版本、内容版本及发布时间 |

API 仅以只读方式打开数据库，不在启动时创建或补齐词条。缺库属于尚未配置，损坏、schema 不符和内容非法属于错误；两者不会混为“搜索无结果”。部署时先验证候选再原子替换，不能原地修改正在服务的 SQLite。

## API

| 路径 | 返回与边界 |
|---|---|
| `GET /api/knowledge/index` | 轻量标题、简释和别名索引，带 `release_id`；缺库时返回 `status=unavailable` |
| `GET /api/knowledge/terms/{term_id}?release_id=...` | 正文、关系、来源和页面绑定；词条不存在为 404，版本改变为 409 |
| `GET /api/knowledge/search?q=...&limit=20` | 标题及别名检索，返回匹配候选，支持短中文词 |

索引与详情支持 ETag。API 的 `release_id` 是覆盖完整内容的摘要令牌，`release_label` 是库中填写的可读发布名称；客户端将令牌视为不透明值。前端按令牌缓存，遇版本变化刷新索引并重新获取详情，避免新标题配旧正文；即使维护者再次使用早期发布名称，内容改变也不会复用旧缓存。损坏库返回 503，不影响估值接口。接口不返回服务器本地路径。

## 内容准备与发布

命令在仓库根目录运行，Python 依赖沿用 `valuation/backend/pyproject.toml`：

```bash
# 可选：仅初始化空库，不生成示例定义。
PYTHONPATH=valuation/backend python -m tools.knowledge_db init --release-id empty-v1

# 内容编辑、来源审核完成后，从 workspace 内的 JSON 清单构建。
PYTHONPATH=valuation/backend python -m tools.knowledge_db build --source workspace/knowledge/content.json

# 独立核验当前或指定数据库。
PYTHONPATH=valuation/backend python -m tools.knowledge_db validate
```

清单顶层为 `release`（`release_id`、`content_commit`、带时区的 `published_at`），及 `terms`、`aliases`、`relations`、`sources`、`citations`、`bindings` 数组。词条哈希和别名归一化由构建器生成，不手写。精确字段、校验和 CLI 参数以 `valuation/backend/knowledge/schema.py`、`valuation/backend/tools/knowledge_db.py` 及 `python -m tools.knowledge_db --help` 为准。每个内容版本使用新的发布 ID；不得给不同内容复用同一个发布 ID。内容库、编辑稿和本地 SQLite 辅助文件均不加入 git；仓库只保留 schema、读写工具、UI 代码和合成测试。

首版编写时，概念定义和公式须引用达摩达兰原始教材或官方公开资料，注明适用条件；AlphaLake 的实现方式和限制单列在页面绑定中。测试通过只证明交互及数据契约可用，不代表尚未编写的词条已完成方法审核。

## 开发检查

```bash
PYTHONPATH=valuation/backend python -m pytest -q valuation/backend/tests/test_knowledge.py
cd valuation/frontend
npm ci
npm run build
node scripts/check-ttm.cjs
npx playwright install chromium
npm run test:knowledge
```

测试只使用系统临时目录或合成网络响应，不依赖真实词条库。数据库内容缺席是本阶段正常交付状态；后续可独立准备首版内容，无需再改估值计算逻辑。
