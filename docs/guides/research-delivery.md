# 研究报告交付与阅读契约

2026-10-18用户批准新增研究阅读页，并将今后研究结果统一放入`workspace/research`。该授权只增加报告整理和只读阅读，不改变估值引擎、原生估值流程或默认参数。

## 目录与生命周期

每份正式交付使用稳定ID，小写字母、数字及连字符，最多80字符，例如`anker-initial-20261017`；显示标题可调整，计算run ID另行记录，不与报告ID混用。更新研究另建ID，可用`previous_id`关联前版，旧运行不覆盖。

```text
workspace/research/<id>/
  report.md             # 中文主报告
  assumptions.md        # 按需附文
  model.xlsx            # 按需交付的模型
  research.json         # 最后原子写入的登记，不是财务数据库
```

研究正文、图表、模型及最终计算结果不提交Git。仓库只维护接口、页面、工具、测试及本规范；`workspace/derived`继续放临时计算、内部验收、同步收据和已有冻结运行。源PDF只引用规范来源归档，不复制进报告。已有冻结计算可继续原地保存，由报告显式登记引用；新研究优先将最终交付组织在本目录。

目录中出现有效`research.json`才进入列表。写正文／附件、准备元数据、校验后执行发布；HTTP接口只读，没有上传、编辑、重新计算或自动行情刷新。

## 登记格式

固定契约`alphalake-research-v1`。必需`contract/id/title/summary/kind/files`；`report_file`默认`report`。种类为`initial_coverage/thesis/independent_dcf/narrative_batch/research_note`；状态`complete/partial/archived`只表示研究交付状态，不是参数批准或预测认证。

`published_at`由发布工具记录真实登记时点；`research_date`保留原研究日期，未知为空。财务期`financial_period`、带时区`information_as_of`及股本日`share_date`分别记录，不能借文件时间或登记时间补造。`tickers`、`run_ids`、`limitations`、`previous_id`按实际给定。

每个文件登记稳定`id`、中文`label`、**workspace相对**`path`和`sha256`。主报告及附文是UTF-8 Markdown；图片、PDF、CSV、JSON、Excel和ZIP等可作为明确登记的附件。文件可引用本包或已有workspace证据；不允许绝对路径、`..`、反斜杠、逃出workspace的软链接、数据库或可执行网页。未登记文件不能经HTTP读取。

元数据草稿无需手填哈希及`published_at`，例如：

```json
{
  "contract": "alphalake-research-v1",
  "id": "anker-initial-20261017",
  "title": "安克创新初始覆盖",
  "summary": "增长、利润正常化与资本投入的三套条件；每股值沿6月末资本结构。",
  "kind": "initial_coverage",
  "tickers": ["SZSE:300866"],
  "research_date": "2026-10-17",
  "financial_period": "2026-06-30",
  "information_as_of": "2026-10-17T04:07:50.458857+00:00",
  "share_date": "2026-06-30",
  "limitations": ["期后融资现金及股本尚未成套重建，不是当前目标价。"],
  "files": [
    {"id": "report", "label": "研究正文", "path": "research/anker-initial-20261017/report.md"},
    {"id": "assumptions", "label": "年度假设", "path": "research/anker-initial-20261017/assumptions.md"}
  ]
}
```

从仓库根目录、已配置后端环境执行：

```bash
PYTHONPATH=valuation/backend .venv/bin/python -m tools.research_reports publish \
  workspace/research/anker-initial-20261017 \
  --metadata workspace/derived/research-publish/anker-initial.json

PYTHONPATH=valuation/backend .venv/bin/python -m tools.research_reports validate \
  workspace/research/anker-initial-20261017
```

工具核对全部登记文件、填充哈希，最后原子写`research.json`；已有登记不覆盖。新版本使用新ID；新文件应先写好再登记。正文可用相对链接指向附件，浏览器按当前文档路径与登记清单解析；Markdown附文在同页阅读，PDF及模型通过文件入口查看／下载。项目操作指南不自动开放给浏览器；确需作为研究依据时附一份有来源的文本快照，或链接知识库／官方原始方法。

## 页面与API

- `/research`无需先加载估值公司，按研究日期排序（未知日期列后，不借登记时间充当研究日），支持标题、公司代码、摘要搜索、类型筛选与分页；缺目录和空目录明确显示暂无报告。内部测试报告不冒充正式公司研究。
- `/research/:id`可直接访问和刷新；显示类型、研究日、财务期、信息截止、股本日、限制、Markdown正文、附文及附件。表格独立横向滚动，窄屏可读；不重新计算价值或改写情景。
- `GET /api/research?q=&kind=&offset=0&limit=20`返回轻量列表、总数和无效登记诊断，不把坏登记静默隐藏。
- `GET /api/research/{id}?document=report`返回登记、文件清单及所选Markdown；未指定附文时读取主报告。
- `GET /api/research/{id}/files/{file_id}`只返回已登记、路径合格且哈希一致的文件；PDF／安全位图可内联，其余下载。没有任意路径接口，不开放workspace静态目录。

接口不创建目录或数据库。列表不读取全量正文和附件；详情只核对本次正文，下载时流式核对对应文件。附件缺失可单独显示不可用；正文缺失、登记错误及哈希冲突明确拒绝。正文禁用任意HTML／脚本，公式沿用现有KaTeX安全设置；未登记的本地链接不可跳转，危险协议与未登记图片不加载。

## 迁移与验收

迁移只整理仍有意义、实际面向用户的研究交付，不移动或改写已冻结运行。旧报告转入新目录时一次性重定位相对链接；保留迁移前文本压缩件、原／新路径、哈希和原因。旧冻结验收按原提交及迁移收据复验，不加旧路径别名或兼容端点。PDF不复制。

验收覆盖列表／搜索／分页、详情直接刷新、附文与PDF／模型链接、表格和公式、移动端、空目录／坏登记／缺文件、哈希篡改、越界路径及脚本拒绝；再用真实报告在既有8080服务验收。主库、财务SQLite与KB不因本功能改变。
