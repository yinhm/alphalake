# Damodaran 公司行业名单原始证据

官方来源：<https://pages.stern.nyu.edu/~adamodar/pc/datasets/indname.xls>，也由已归档`capexGlobal.xls`头部给出该地址。感谢 Aswath Damodaran / NYU Stern 提供数据；来源说明见 <https://pages.stern.nyu.edu/adamodar/New_Home_Page/databreakdown.html>。

`indname.xls.gz`是原始22,255,104字节XLS的无损gzip（零mtime），解压SHA256=`a1f4d70aa2cff728d846ca0b2fbdf7ea94030d2df6e140418920d3addb22c363`。URL、原始哈希和首次取得时间保存在`indname.source.json`，没有修改单元格或裁剪工作表。

三张表`By industry`、`By company name`、`By geography`各48,156条数据、8列，行的多重集合完全相同。这是同一来源的重复表示校验，不是三份独立证据。94个行业名与已审核Beta/资本效率行业目录匹配；12条没有ticker，仅原始证据保留并单列计数，不猜身份。非空ticker没有重复。

当前解析范围为2,225条`SHSE:`和2,875条`SZSE:`记录，合计5,100；尚不能称5,100家当前A股公司，须再经过业务时点身份匹配和本地证券类型/币种范围筛选。其他43,056条留在原始证据中，范围外计数包含12条无ticker记录。`BSE:`这里是印度市场记录，不能映射北交所。`Country`是来源国家字段，不能代替上市地：安道麦`SZSE:000553`为Israel、中国中铁`SHSE:601390`为Hong Kong，仍保留其沪深上市标识。

原始示例：安克`SZSE:300866`→`Computers/Peripherals`；茅台`SHSE:600519`→`Beverage (Alcoholic)`；海天→`Food Processing`；小熊→`Furn/Home Furnishings`；汇川→`Machinery`。这是提供方分类，不自动覆盖已有分析政策的行业代理，也不证明混合业务适用单一DCF模型。

本工作簿没有明确可核验的观察日期或发布日期，解析输出`source_observation_date=null`，不得借用资本效率文件的2026-01-05日期。中国中免（`By company name`第8354行）、厦门信达（第46752行）的SIC列为数值0，其他目标行该列为文本；保留原JSON类型，不把0改成空值或行业结论。

首版严格限定本次审核的表头、三表范围、94行业、48,156总行及5,100沪深行；上游改变范围时先拒绝，需重新核验后升级解析器。Go真实解包/解析/负向验证由CI设置`ALPHALAKE_TEST_PYTHON=python`强制运行；Python测试另篡改一张表的行业单元格，必须拒绝不一致。此阶段仅完成源解析，尚未发布生产分类、完成上市身份匹配或扩大估值成功数。
