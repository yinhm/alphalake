# 原生参考输入与版本契约

已接入的Damodaran参考存于主库，随财务导出SQLite，并绑定到原生会话。保留原生默认US口径和用户显式覆盖；Global行业数据不静默替代US数据。当前版本、截止和交付数量见[项目状态](../implementation-status.md)。

## ERP语义

源`total_equity_risk_premium`已含国家风险溢价。原生`MacroInputs.equity_risk_premium`以源总ERP减源CRP得到基础分量，引擎只加一次CRP；国家目录与地域分部直接使用源总ERP。缺分量不填零，不以统一成熟市场ERP覆盖国家源行。

依据为[Damodaran国家风险说明](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ctryprem.html)。这一口径修复和参考版本更新须分别比较，不混称单因素估值变化；完整纠错金额与原始验收见[历史记录](../history/native-reference-bridge-20260927.md)。

## 接口与数据边界


- `upgrade-native-references <db>`：当前显式schema54→55，增加研发/租赁调整后利润率指标；原schema53→54转换按原提交执行；先备份，重开并比较后清理回滚副本。
- `sync-native-reference <db> --file betas --local workspace/damodaran/betas.xls --python .venv/bin/python`：导入已有原文件。`--local`、`--offline`互斥；前者按此次导入记首次取得，不猜早期下载时间；后者只重放已注册且哈希合格的发布。省略两者才访问该工作簿原官方URL。支持清单见解析器及离线证据`internal/source/damodaran/testdata/native/sources.json`。
- `export-native-references <db> --as-of <RFC3339>`：在同一事务选取已完成发布，导出发布ID、源定位、SHA256、观察日、取得日、数值与缺项；归档缺失/坏哈希拒绝。
- 当前SQLite导出契约为v9、原生参考契约为v2，包含`reference_release`、`reference_value`、`reference_company`、`reference_issuer_association`及内容签名。默认财务与参考同一截止；`--reference-as-of`可显式选择不同参考截止，必须分别报告，不能冒称严格历史PIT。旧契约必须显式重建，不作运行时回退。
- 原生默认仍是US；最近验收US/Global各94行业、21指标已解析，共3,948行业观测（其中两项调整后利润率为schema55新增）。来源金额按既有DECIMAL12规范存储，逐格复核允许最多半个末位量化误差，不声称恢复源Excel浮点无限精度。原生股票波动率读取WACC表同名字段；EVA债务资本比独立为`debt_capital_ratio`，不再覆盖Beta表`debt_equity_ratio`。
- 国家风险复用主库CN/HK/US/IL已审核发布；国家税共4项，其中CN=25%、HK=16.5%、IL=23%，US为空且状态`ambiguous`。源表B217、B246分别为25.63%、25.886141%，没有依据选择其中一条，禁止最后一行覆盖。US税自动选择和未接入国家须拒绝，用户全量显式参数API仍保留；当前目标仍是沪深普通企业，不宣称全球国家覆盖。
- 合成评级、区域/分位数等原引擎冻结参考JSON暂保留原方法版本，并在快照中单列`model_reference_hashes`。这些**不是同步的当前市场参考**；不拿主库大企业信用档替换不同边界的原表，不把默认WACC称公司已核验市场WACC。用户无风险利率、方法与显式参数优先，不强制自动替换为人民币国债。
- 新会话携带`reference_snapshot`，PATCH和敏感性计算继续使用创建时的参考对象；新发布只影响新会话。原页面不改、不要求用户上传参考JSON；未知行业不猜、不静默改Global或任意占位行业。

同发行人参考按[审核和发布流程](../guides/system-delivery.md#同发行人参考的默认关联)接入：人工核实同一法人及A/H代码后，复用原H股来源的行业/国家；不猜集团关系、不增加H股价格，精确A股条目和用户覆盖优先。关联随快照版本绑定会话，撤销及工作簿更新不会静默回退旧审核。

源证据补充：税率表重复美国行、Beta去杠杆税率选择、EVA的D/(D+E)与Beta的D/E均锁定为离线回归；21个字段按精确表头/出现序提取，缺失保持NULL。在线更新的文件如改版不通过这些门槛，不发布替代可信历史。

## 消费和发布

财务准入与参考准入分别返回；缺公司行业/国家、歧义税率或失效发布不能靠任意占位值转为自动就绪。用户显式选择属于假设，不写回公司分类。冻结模型参考、同步市场参考、用户覆盖分别留痕，默认WACC不冒称公司已核验市场WACC。

日常同步、导出、候选API核对和原子发布使用[系统交付](../guides/system-delivery.md)；底层字段及快照契约见[SQLite导出](../guides/valuation-sqlite-export.md)。验收须检查原生创建会话、修改参数和敏感性计算仍绑定同一参考版本；仅入库成功不算消费链路通过。
