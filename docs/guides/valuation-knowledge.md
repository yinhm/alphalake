# 估值知识库：代码与内容契约

本功能为原生估值页面增加词条阅读能力。代码随仓库交付，词条数据库和编辑稿保存在 workspace，不随代码提交。当前本地中文内容已扩展为可自学的估值材料；版本及验收见[实现状态](../implementation-status.md#知识库)。词条服务不依赖估值会话，不参与估值计算。

## 阅读入口

- `/knowledge`：默认学习首页，未创建估值也可访问。包含七课自学路线、六类主题导航及案例、经济策略、预测参数、系统初值、货币WACC、结果检查等专题入口。入口复用同版词条索引的稳定ID与标题，未发布内容不生成链接；不逐篇加载正文或另建导航数据库。
- 首页右侧为搜索与词条目录，窄屏依次展示学习导航和检索，可用页首“直接搜索词条”链接定位并聚焦输入框。目录和搜索结果最多展示10条；学习和专题导航不属于完整词表。搜索匹配完整词库，精确中英文名称及别名优先，剩余包含匹配保留原顺序。结果较多时提示细化关键词，其余词条也可通过直接链接或关联阅读。
- `/knowledge/learning-roadmap`：中文学习路线，依次进入报表、现金流、增长与资本、风险、终值、股权桥接和完整DCF；各课包含主题词条目录。
- `/knowledge/learning-practice`：概念对照、制造/研发/成熟企业贯穿案例、自测与估值检查指南。教学参数不用于自动覆盖公司输入。
- `/knowledge/forecast-assumptions`：默认参数与预测研究专题，按收入、利润率、资本效率、联合预测、验证、原始研究和自测阅读；同时区分当前实现及未实施的研究建议。
- `/knowledge/initial-valuation-assumptions`：有来源的增长、利润率及资本初值、稳态编辑、保留条件与覆盖行为；不是预测认证。
- `/knowledge/valuation-strategy-selection`：按经营阶段、经济特征与存续条件选择策略，连接周期、年轻、成熟、衰退、困境、研发、扩产、有限寿命、监管、多业务及金融边界；附策略自测。
- `/knowledge/narrative-to-numbers`：从经济特征、经营故事到数字和现金，解释达摩达兰的五步、证据与反例；配套 `/knowledge/practice-narrative-to-numbers` 六题自测。可从首页的经济策略、学习路线和预测专题进入，也可搜索 Stories to Numbers。
- `/knowledge/foundations-finance`：达摩达兰免费金融基础课程的中文自学入口，覆盖全部12讲，配官方YouTube、原版幻灯片、测验和答案链接。可从首页专题、学习路线或搜索进入。
- `/knowledge/foundations-finance-01`至`-12`：逐讲中文讲义；加`?view=slides`进入HTML幻灯片。课程讲义可切换连续阅读与翻页，上一讲／下一讲保留当前模式，转到普通概念时回到连续阅读。
- `/knowledge/accounting-course`：达摩达兰免费会计课，按原目录先学报表、调整和比率，再读各自的公司实例。11讲、142页原课译文及10组50道官方测验／答案；讲义ID为`accounting-01`、`-02`、`-02a`，依此至`-06a`，同样支持`?view=slides`。
- `/knowledge/statistics-course`：达摩达兰统计课，13讲加预览共189页、13组65题；讲义从`statistics-01`至`-06b`及`statistics-preview`，按原课含A／B应用讲。
- `/knowledge/valuation-course`：达摩达兰估值课，25讲295页、25组129道有效题；讲义ID为`valuation-01`至`-25`，附13个实务工具专题原链接，其中5份45页讲义和1篇作者文章另有完整中文。两门沿用HTML翻页、连续阅读及折叠答案。
- `/knowledge/investment-philosophies-course`：指定38讲版投资哲学课程，503页中文逐页讲义及38组189题官方测验／答案；讲义ID为`investment-philosophies-01`至`-38`，测验在对应ID后加`-test`。保留官方英文视频及原PDF。
- `/knowledge/<term_id>`：可分享的独立词条页，保留检索与正文阅读布局；“知识库首页”返回学习入口，不在每篇正文上方重复展示整套导航。
- 表格标签旁的帮助按钮：鼠标设备默认隐藏，悬停对应术语或键盘聚焦后显示并打开词条；预留位置避免悬停时文字移动。无悬停能力的触屏设备保持可见，可直接点击。
- 开启“划词解释”后，选取阅读区词汇显示简释，再展开完整解释。已标注标签支持选中部分词汇；普通文字使用中英文标题及别名匹配，有歧义时由用户选择。

视口宽度达到 1600px 时，384px 知识栏与估值主栏并列；更窄时使用可关闭的模态抽屉。导航可收起；知识栏独立滚动。划词弹层不自动夺取焦点，输入框、可编辑单元格及跨单元格选择不触发解释，复制操作保持浏览器行为。划词开关在当前浏览器保存。

长文提供可展开目录，跳转后定位到对应章节；练习答案默认折叠，可由鼠标或键盘展开。课程提供上一课/下一课及所属课程导航。

词条正文、AlphaLake 页面用途和当前估值数值分别展示。当前数值仅由前端 `knowledge/liveValues.ts` 的明确白名单从 `ValuationResponse` 读取；数据库不能执行表达式或读取任意字段，缺值不补零。页面绑定可包含使用限制和所核验的引擎版本。正文支持受限 Markdown 和数学公式，禁用原始 HTML，不加载正文远程图片。

独立页也显示已审核的页面用法；一个词条有多个绑定时，可选择初始/稳态或税前/税后等明确字段。加载估值后，展开“本次输入、计算分支与来源”查看响应原值、公司、财务期间、信息截止、币种、行业地域、参考快照及会话。字段来源优先精确记录，共享假设记录明确标为共享；缺记录不猜默认或用户覆盖。此面板是相关输入展示，不构建第二套依赖图、金融公式或经济合理性认证；为空的覆盖输入不表示实际计算为零。切换公司、更新参数及清除会话都使用当前响应，不保留上一公司的数值。

稳定的页面绑定契约由 `knowledge/liveValues.ts` 中的 `KNOWLEDGE_BINDINGS` 导出。内容准备时须同时匹配 `binding_id`、`term_id` 和 `field_path`；例如 `wacc.current` 绑定 `wacc` 与 `cost_of_capital.wacc`，`wacc.stable-override` 绑定稳定期覆盖输入，不能互换。多年度表格中仅标注概念，不把某一基年值当成所有列的共同值。

## 存储边界

默认位置：`workspace/knowledge/knowledge.sqlite`。`ALPHALAKE_WORKSPACE` 可改变 workspace 根目录，`ALPHALAKE_KNOWLEDGE_DB` 可显式指定知识库文件。该 SQLite 是编辑性知识内容，与主 DuckDB 中的财务事实、参考数据及 `valuation.sqlite` 财务发布快照职责不同，不新增财务权威来源。

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
| `GET /api/knowledge/assets/{sha256}.png` | 只读本地内容寻址PNG；核对文件头与哈希，不沿软链接读取；不存在404，损坏503，支持ETag304 |

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

正文以独立编写的中文教学解释为主，可以使用通行的财务与会计知识；达摩达兰原著作为估值方法和延伸阅读参考，不要求每句话都取自原著。方法、公式及适用条件仍须准确并与项目的方法约定一致，不能把作者姓名当作未经核验的背书。来源区分字段定义、列报背景与方法参考；原创洞见和虚构算例不能冒称原著引文。当前编辑稿使用 `workspace/knowledge/content.json`，原始资料按来源归档；引用注明文件内从1起算的PDF页序，不能冒称纸书页码。未知原始发布日期留空，核验日期独立记录。教学数字不是公司事实、预测或默认参数。

正文的“AlphaLake 当前用法”用于独立阅读；页面绑定另外说明精确字段、限制与核验实现版本，本次数值仍由既有响应白名单读取。历史利润率不必然是未来目标；原生合成评级、默认US参考及终值代理等已知边界须明示，不用理论解释追认本地实现。内容未在Git保存时，`content_commit` 留空；绑定的 `reviewed_engine_ref` 记录代码版本，内容由发布标签、完整摘要令牌及编辑稿哈希追踪，不冒称代码提交包含词条正文。

发布前核验中文正文、全部已标注术语、绑定三元组、来源定位和关联词条，并检查正文篡改拒绝、版本冲突、真实接口及浏览器公式。原文位置可重提取不等于独立语义审核，也不认证任何公司的经济假设。首版来源收据与验收记录见 `workspace/derived/knowledge-init/`，当前内容扩展记录见 `workspace/derived/knowledge-expansion/`；后续沿用构建器原子发布，不原地编辑正在服务的数据库。首次接入新路由时重启现有后端；后续只更新内容库时无需重启。

## 字段覆盖与页面关联

覆盖清单分别核对标准三表具名字段、原生基础财务接口及实际页面标签，不能只统计词条总数。当前清单位于 `workspace/derived/knowledge-fields/coverage.json`；发布数量和版本见实现状态。源位置无公开定义时不猜词义；字段收录不等于映射已审核、已有数值、模型已消费或金融模型获准。

共用 `Term` 优先使用显式稳定ID；未标注的只读表格标签/表头和资本成本标签按完整中文名、英文名或别名精确匹配，只有一个候选才出现解释按钮。动态币种/年份标签使用明确ID，不截取字符串猜词；多义标签保留划词候选，不擅自选择。编辑格和财务数值格不自动匹配，词条不改变原输入及计算；新增普通字段解释通常仅需补内容和准确别名。

别名不是会计等价声明。必须分别解释营业收入与营业总收入、营业利润与报告EBIT、合并与归母净利润、扣非与估值调整净利润、现金组合与超额现金、债务余额与净发行、股息收益率与分红率。累计、单季度、期初/期末、每股及比率的聚合条件写入正文，源比率未经核验不反推供应商分母和年化公式。财务字段含义以官方源目录及适用会计列报为依据，估值方法以达摩达兰原著为依据；不能把国内列报差异称为作者的方法规定。

## 自学内容编写约定

站在教师的角度，让初学者先明白概念，再掌握洞见。每篇至少回答：它是什么；哪笔交易或经营行为产生它；为什么有用；最容易误解的地方，以及这种误解如何改变分析。缩写先展开，用日常语言解释专业词；不要以“核对范围”“不是现金流”等一句提醒代替原因。

正文按题目需要组织，通常先讲基本解释和一项具体洞见，再用算例说明，随后讲用途、条件与误区，最后放项目口径和关联阅读。不要为了凑齐固定八节而重复内容；没有独立公式的报表科目，无需塞入通用余额公式。自测应有真实推理和可检查答案，不复述上一段。当前渲染器不支持Markdown表格，多步金额使用分年或分步骤条目。

洞见必须解释机制。例如EBITDA应先展开息、税、折旧和摊销，再讲“折旧本期不付现金，但机器在消耗，加回折旧并不会免掉更新设备的钱”。例子从收入走到EBITDA、EBIT及净利润，再明确经营税与报表税的不同，桥接到扣除再投资后的FCFF。类似地，应收讲企业替客户垫资，减值讲非现金调整不消除经济损失，ROE讲业务回报与杠杆的区别。公式的变量、单位、期间和现金归属服务于这些解释，不能替代解释。

内容按报表、经营现金、增长与再投资、风险、成熟期、股权索取权及方法边界组织。新增词条加入相应课程目录和关联，旧ID和既有页面绑定保持稳定。关键词有歧义时保留候选；检索别名不表示两个标准财务字段可以互相替代。

目录识别行首的二级标题 `## 标题`，忽略围栏代码内的同形文字；至少三节时显示目录。答案使用独立二级标题 `## 答案与解析`，由原生 `details/summary` 折叠，不在正文开启任意HTML。课程导航复用 `kb_relation` 的“上一课”“下一课”“所属课程”，目标必须是已发布词条；不另建课程数据库或账户系统。

课程讲义使用`kind=课程讲义`；HTML模式将同一正文的二级章节逐页展示，不另存一份幻灯片正文或执行HTML。提供上一页／下一页、章节选择、页数及局部左右方向键、Home／End；输入框及选择框保留浏览器键盘操作。切换讲义或内容版本从第一页开始。窄屏使用相同页面，隐藏检索栏以留出阅读空间；公式继续使用既有安全Markdown/KaTeX渲染。普通词条即使带同样查询参数，也不会成为课程幻灯片。

跨章节案例使用同一虚构公司并独立复算：利润和资本同范围，投资与收入时序匹配，稳定增长有再投资支持，经营价值到每股值的单位与索取权闭合。当前手算入口为 `/knowledge/lesson-full-dcf`。案例参数不是公司事实、原生默认或作者统一规定；介绍相对估值、金融或短历史方法不授权新增业务算法。

新增三个案例分别见 `/knowledge/case-manufacturer`、`/knowledge/case-rd-business` 和 `/knowledge/case-mature-business`。研发例明确当期税前扣除及即时利用条件，补研究费用与摊销差额的税收调整，逐年维护四年队列；收入停止增长后继续展开四年队列替换，再进入稳定终值。税收优惠和亏损企业不能无条件照搬；教材方法与原生引擎实际分支分开说明。案例独立复算只作教学校验，不提供业务估值接口。

更新正文核对涉及的方法依据与当前实际分支，再建立新版本候选，检查链接、引用、全部绑定和算例，最后原子替换并重开接口/网页。使用过的发布ID不能承载不同内容，候选修订同样换ID。保留原编辑稿、出处哈希和失败记录；临时候选与回滚库在复验后删除并留摘要收据。内容字数、章节数、自动结构检查或重复提取不能代替经济语义审核；合法纠错应解释，不因旧算例已发布就继续保留错误。教学重写验收记录见 `workspace/derived/knowledge-teaching/`，专题及阅读功能验收见 `workspace/derived/knowledge-learning/`。内容仍由workspace内编辑稿重建，代码仓库不含完整正文，裸克隆不会自动拥有此本地内容发布。

## 达摩达兰金融基础课程

来源为[官方免费课程](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/webcastfoundationsonline.htm)，[开放课程博客](https://aswathdamodaran.blogspot.com/2024/12/for-fun-of-it-open-house-for-my-spring.html)仅用于核对Online (free)入口。课程依次讨论金融概览、企业结构、现金流、风险、风险衡量、货币时间价值、债券、股权、期权、通胀、利率和汇率，不把博客年份当所有资料的版本年份。

按用户2026-10-11明确要求，原课改为逐页中文译本：12讲148页幻灯片，一页PDF对应一页HTML，包含封面；原页印刷编号与从1起算的文件页序分别理解。11组官方测验共53题及答案译为独立练习词条，不增加幻灯片页数；第1讲原目录没有测验。上一版原创例子、36道自测及119页教学章节退出当前课程，旧内容压缩留作历史。本轮按用户确认可翻译的授权进行，作者署名与官方出处保留。

保持原例的历史日期、币种、数值、公式、观点与选项顺序，不用当前市场参考替换。原图与中文标签译文并列：34张图像是原PDF页的原样转换，图内英文像素未改写；文字及图表标签在同页中文正文中译出。疑似原算式、单位、答案标号问题保留原写法，独立复算另标“译注”，不能静默纠正后冒称原作者已修订。视频仍是12个官方英文YouTube外链，未下载、转录或制作中文字幕。第9讲答案PDF的前两页重复题卷，中文题目只呈现一次，末页答案完整翻译。

编辑来源仍是`workspace/knowledge/content.json`，翻译稿为`workspace/knowledge/courses/foundations-source-translation.md`及`foundations-tests-translation.md`；人工修订后更新清单，并沿既有构建器验收。原目录HTML与34份PDF平铺归档于`workspace/damodaran/`，文件名为`foundations-online-course.html`及`foundations-session-NN-slides/test/solution.pdf`。逐文件收据在`workspace/derived/knowledge-foundations/sources.json`，记录官方目录原链接、实际官方下载URL、哈希和取得状态，按现有文件复用，不重复建按日期的课程目录。

课程图片放知识库文件旁的`assets/<sha256>.png`，默认即`workspace/knowledge/assets/`；不加图片表，不放workspace顶层或代码目录。正文只允许精确`/api/knowledge/assets/<64位小写SHA-256>.png`图片地址，不加载远程图片、任意相对路径、SVG、HTML或脚本。图片先写入并核验，再原子发布引用它们的SQLite；回滚期间旧版本引用的图片须保留，撤除后可按全部保留版本的引用集合清理。图片使用内容哈希及immutable缓存，库的发布令牌仍约束索引、正文与引用；部署须同时携带实际引用的图片，裸克隆没有正文或图片。

本轮`www.stern.nyu.edu`下载返回403，同路径`pages.stern.nyu.edu`成功；两者都属NYU官方来源，不将下载域名变更说成课程内容更新。未知出版日或未记录的首次取得留空，核验日不代替出版日。资料包含历史图表，不能直接当作当前市场参数。来源哈希和PDF文本提取只能检查文件完整性与主题，不等于逐句语义审计。视频使用外部链接，不下载、内嵌或声称已核验全部播放。

课程关系、导航、检索、来源和发布沿用现有七表schema及只读API，不引入课程账户、学习进度或第二套金融计算。逐页译文、图像哈希、原课习题复算、实际页面及缓存验收见[翻译交付](../history/kb-foundations-translation-20261011.md)；原教学配套退出前的验收见[历史记录](../history/kb-finance-foundations-20261011.md)。

## 达摩达兰会计课程

来源为[官方Accounting免费课程](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/webcastacctg.htm)。原课从财务报表使用者的角度解释会计，保留作者对准则及估值调整的观点。按原目录顺序提供1、2、2A、3、3A、4、4A、5、5A、6、6A，共11讲142页中文幻灯片；第1讲没有测验，其余10组各5题，答案默认折叠。预览和11讲视频保持官方英文YouTube外链。

沿用金融基础课程的逐页翻译和图像规则，包含封面，一页PDF对应一页HTML。保留原公司实例、历史年份、币种、公式、数值及答案；63张原页图像配同页中文表头、标签和说明，图内英文像素不替换。原租赁现值、公司名称及答案口径有差异时，原数和独立复算分别呈现并加译注，不静默修正原文，也不把原课历史税制、会计判断或比率表当当前规则、已核验公司事实或项目估值政策。

编辑来源仍为`workspace/knowledge/content.json`，翻译稿在`workspace/knowledge/courses/accounting-source-translation.md`和`accounting-tests-translation.md`。原目录及31份PDF平铺放`workspace/damodaran/accounting-online-course.html`、`accounting-session-<讲次>-slides/test/solution.pdf`，讲次使用小写如`2a`。来源、逐页对应、图像转换、算术差异和发布收据在`workspace/derived/knowledge-accounting/`；图片复用`workspace/knowledge/assets/`的内容哈希路径，不复制源PDF到KB或派生目录。

课程复用现有七表、导航、检索、折叠答案和只读API，首页只增加已发布会计课程入口；现有词条及页面绑定保持。正文和资料不随代码提交，裸克隆须单独准备内容。内容覆盖、独立算术、实际桌面／窄屏和发布验证见[会计翻译验收](../history/kb-accounting-translation-20261011.md)。

## 达摩达兰统计与估值课程

[Statistics官方目录](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/webcaststatistics.htm)按1、2、2A、3、3A、4、4A、5、5A、5B、6、6A、6B提供13讲，另有6页预览；共189页、65题。覆盖抽样、描述统计、分布、回归、概率、决策树及模拟；金融应用与原课历史数据保留。目录中的5处空锚点不作为资料，其中部分误指其他讲答案；只采用可见的Slides／Post-class test／solution链接，保留原HTML及失败收据。附原作者5项工具说明和35条阅读链接：作者文章《DCF误解3.2》提供完整中文，另外34条第三方阅读保留标题、作者及原链接。

[Valuation官方目录](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/webcastvalonline.htm)包含25讲295页和25份合并题卷／答案，129道有题干问题。目录第20讲起与原封面的19A、20至24编号不一致，导航依官网25讲序，原封面编号在译文注明；末卷空“6.”不编造题干、不计有效题。附13个实务视频及配套Excel／报告／讲义外链；其中读10-K、隐含ERP、价值定义、倍数回归和专利等待期权五份作者讲义共45页／张，已逐页译为中文，另附《价值的纠缠》完整中文文章。原工作簿、公司申报文件及第三方参考保留原链接，不重新实现其模型或下载视频。资料版本按文件原例，不把课程入口或取得年份当市场参考日期。

两门按现有逐页翻译契约发布：一页PDF一页HTML，包含封面，讲授文字、公式与图表标签用中文；必要原图保留英文像素。原统计标准误、p值软件异常、R平方条件，及估值租赁／研发、现金桥接、答案编号和算式错误分别保留原值并加译注，不能静默修正后称原作者版本。历史税制、私人／金融／期权等教学不授权扩大本项目模型范围，也不改当前默认参数。第三方漫画、新闻和论文正文不另复制；相关页保留原PDF定位。

编辑稿仍为`workspace/knowledge/content.json`；四份新译稿为`workspace/knowledge/courses/statistics-source-translation.md`、`statistics-tests-translation.md`、`valuation-source-translation.md`、`valuation-tests-translation.md`。两个官方HTML及90份PDF平铺放`workspace/damodaran/`，使用`statistics-<讲次>-slides/test/solution.pdf`与`valuation-NN-slides/test-solution.pdf`；按哈希复用，不在派生目录复制PDF。新图复用既有`assets/<sha256>.png`及只读API，不新增表。

来源和失败收据分别在`workspace/derived/knowledge-statistics/`、`workspace/derived/knowledge-valuation/`。逐页对应、结构、原例算术、篡改拒绝、真实API和桌面／窄屏验收见[课程交付](../history/kb-statistics-valuation-20261013.md)。动态正文、发布库及图片不随Git，部署仍需携带内容；临时转换图、候选及回滚副本复验后清理。既有词条正文与全部页面绑定保持，代码只为首页补两门课程卡片及导航回归。

## 达摩达兰投资哲学课程

入口为[官方38讲目录](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/webcastinvphil.htm)，不自动混入官网另设的新版本。按原讲次组织风险、报表、估值和交易成本基础，以及技术分析、价值／成长投资、信息交易、三类套利、市场择时和被动投资；书章编号与讲次分别保存。

按用户确认的翻译授权，当前为完整原课中文译本：38讲503页幻灯片，一页PDF对应一页HTML，包含封面；38组官方测验共189道主问题与答案，单独提供中文练习词条。沿用原目录、原讲次和既有讲义ID，替换此前提要；不另编教学配套，也不把测验PDF重复的题干再计一次。

保持原历史年份、币种、公式、数值、观点及选项顺序。155张原课页图像配同页中文坐标、图例、列名与说明，图内英文像素保留；第三方终端／行业轮动截图只保留原页链接及标签译文。原件被页脚遮住的文字明确标识，不补造。原公式、题干／答案版本及标号冲突单列译注，独立复算与原打印数分别呈现，不替原作者静默修订。视频仍是官方英文外链，未下载、转录或制作中文字幕。

76份PDF及目录HTML平铺归档于`workspace/damodaran/`，文件名为`investment-philosophies-NN-slides/test.pdf`；复用本地原件，不重复下载或复制到KB。中文稿为`workspace/knowledge/courses/investment-philosophies-source-translation.md`与`investment-philosophies-tests-translation.md`，构建清单仍为`workspace/knowledge/content.json`。图片沿用内容哈希assets及只读API，不新增表或产品依赖。

来源、页数、原文问题、独立算术、候选与发布收据放`workspace/derived/knowledge-investment-philosophies/`；见[逐页翻译交付](../history/kb-investment-philosophies-translation-20261014.md)。此前[提要交付](../history/kb-investment-philosophies-20261014.md)只作历史记录；旧编辑稿压缩保留，提要退出当前课程。既有680篇正文及34项估值页面绑定不变，历史实证不作为当前A股事实，课程不授权新增业务估值能力。

## 课程完整性复核与补充译文

2026-10-14按用户要求全面核对上述五门课程：主课100份讲义1,277页、97组486题均有中文；补齐5份45页估值实务讲义、2篇作者文章，并修订11讲中的13页。中文正文保留原材料的历史公司、数值及观点；原文错误或展示精度不足时，原值与独立核算分别说明。图像保留原像素，同页中文译出标签、坐标和必要表格，不把图片数量当翻译完整性证明。

新增讲义ID为`valuation-practice-reading-10k`、`valuation-practice-implied-erp`、`valuation-practice-value-definitions`、`valuation-practice-multiple-regression`、`valuation-practice-patent-option`；仍支持`?view=slides`。文章ID为`statistics-uncertainty-reading`及`valuation-value-definitions-reading`，均从原课程目录进入，不另建课程或阅读模块。

补充译稿为`workspace/knowledge/courses/valuation-practice-source-translation.md`及`course-readings-source-translation.md`，使用实际发布的本地图片地址；既有受修订主课译稿同时更新。新增原件平铺于`workspace/damodaran/`，文件名前缀与词条ID一致；ERP原件为PPT，按实际7张含隐藏页导出核对，不将失效的文档页数元数据当实际幻灯片数。来源、原文差异、结构、公式、发布及浏览器收据位于`workspace/derived/knowledge-course-audit/run-kj1dr1uo/`，见[完整性验收](../history/kb-course-completeness-20261014.md)。

本次翻译范围是原课讲义、测验／答案及作者配套讲义／文章；视频保持官方原链接，第三方书籍、论文、软件网站、公司申报文件和原生工作簿按参考资料提供，没有递归复制全部外链正文或制作视频字幕。正文、原件及发布库仍只在workspace，代码提交不包含动态译文；跨机器部署须携带知识内容和实际引用图片。

## 默认参数与预测研究内容

专题由八篇中文学习材料组成：入口 `forecast-assumptions`，分项 `revenue-forecasting`、`margin-forecasting`、`capital-efficiency-forecasting`，联合检查 `joint-financial-forecasting`，验证 `forecast-validation`，文献导读 `forecast-research-evidence`，自测 `practice-forecast-assumptions`。课程、相关词条及检索别名复用已有机制，不新增估值计算或界面。

未来修订须分别说明原生默认、已有具名条件和研究候选：最新同比不等于长期增长，历史利润率不等于可持续目标，存量周转不等于新增投入效率。短期预测检验和长期经济过渡分开；统计观察窗口不改研发经济寿命。收入、调整利润、净投入、研发税基及终值配套，沿用既有显式年度契约和审阅器。直接年度总净再投资已接通，预算与年度倍率互斥、包含资本化研发净投入；不再写成待审批接口。能力之外的绝对收入、有限寿命等输入或算法仍不以微小正数或补零绕过。

论文保留原始链接、发表/修订状态、核验范围和迁移限制；未知精确发布日期留空，不能把摘要核实称全文复现，或将盈利预测改善直接解释为DCF改善。原始方法、公司依据、本地政策分别记录。知识内容提供研究思路，不构成新算法、默认替换、历史扩采或自动采用授权。内容与发布证据在 `workspace/derived/knowledge-forecast/`，本轮验收见[研究专题发布记录](../history/kb-forecast-assumptions-20261004.md)。

## 经济特征与估值策略内容

入口 `valuation-strategy-selection` 复用既有课程与相关词条，新增 `capital-intensive-valuation`、`finite-life-valuation`、`regulated-business-valuation` 和 `practice-valuation-strategies`；扩充既有周期、年轻、亏损、衰退、困境、研发、竞争优势、分部及金融解释，不重复建同义词条。

经营阶段决定预测骨架，经济特征决定调整与证据，存续条件决定是否需要失败情景；分类可以叠加。行业只提供发现线索，不按名称、单年亏损或估值高低自动选策略。本文分类是项目教学整理，不冒称达摩达兰的固定分类表。正常化、商业化、衰退和偿债困境分别解释，不统一套用历史利润率恢复。

算例分别核对现金归属、投资时滞、研发税务、有限期限、监管回报及持股范围；机会成本与利润保持税前／税后同口径。新增原始PDF按可识别名称放 `workspace/damodaran/`，引用使用文件页序，来源、修改、算术、候选及发布证据在 `workspace/derived/knowledge-strategies/`。

各篇明确当前能力：已有显式条件与年度总预算可复用；自动策略选择、零收入商业化、完整困境清偿、有限寿命及分部估值等未实施能力仍保留边界，金融继续范围外。教学不授权修改默认或引擎。发布及修正记录见[策略专题验收](../history/kb-valuation-strategies-20261007.md)。

## 估值叙事与经济特征

`narrative-to-numbers` 与 `practice-narrative-to-numbers` 连接既有经济策略及预测材料；扩充原 `value-driver-tree`，不重复建同义概念。学习主线是：经济特征确定问题，经营故事解释需求、竞争及资本机制，数字暴露现金代价，新增证据支持修订。故事卡区分事实、主张、年度条件、支持证据、可推翻条件和替代情景；没有概率校准的故事仍是条件情景。

正文用周期恢复、研发商业化、扩产、成熟优势及有限期限解释差异。算例明确币种、经营范围、税务和投入时差；当年投入例与既有一年领先案例不混用，资本化研发的利润、税基和净投入桥须同时闭合。税前回报、税后ROIC、存量周转与新增倍率分别解释；ROIC=WACC只作为无新增超额回报条件，不写成所有公司的要求。

当前Stories to Numbers仍为参数、历史／参考、回报诊断和敏感性视图，没有文字理解或合理参数自动选择。回报／终值显示BUG已按[台账E39](../valuation/engine-change-ledger.md#e39stories回报与终值显示bug2026-10-07)修复；独立输入工具已实现[故事证据到年度条件与原生估值](../valuation/narrative-valuation-plan.md)，不新增网页故事编辑器、公式或默认。词条当前用法随实现修正，教学算例及其他554篇不变，见[本轮交付](../history/narrative-valuation-delivery-20261007.md)。三份原始资料平铺归档在`workspace/damodaran/`；此前内容、独立算术、来源定位及发布证据在`workspace/derived/knowledge-narrative/`，见[叙事专题历史](../history/kb-narrative-to-numbers-20261007.md)。

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

仓库回归只使用系统临时目录或合成网络响应，不依赖真实词条库。裸克隆仍需另行准备内容，缺库不能解释为已发布词条；本地内容验收记录独立于合成回归。新增或修订正文无需改变估值计算逻辑。
