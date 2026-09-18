# 苏泊尔2026H1受限资产取证

已从[新浪托管的发行人公告](https://money.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=12563787&stockid=002032)取得133页完整PDF，文件和实际下载URL、时间、SHA-256保存于[回执](receipt.json)。CNINFO原链接返回403；本文件明确作为镜像归档，未证明与CNINFO二进制逐字节相同，未伪装成CNINFO下载；先在主库副本关联，后续已[备份发布主库并重开验收](../../../../docs/reviewed-main-publication-20260918.md)。以下记录原9月17日副本验收；历史回执的未发布状态不追溯改写。

| 分量（元） | PDF金额 | TDX标准金额 |
| --- | ---: | ---: |
| 一年内到期其他债权投资，FN19 | 1,823,720,958.90 | 1,823,720,960 |
| 其中受限，第86页 | 750,000,000.00 | 显式附注，非TDX独立字段 |
| 非流动其他债权投资，FN431 | 967,473,301.38 | 967,473,281.25 |
| 其中受限，第87页 | 20,000,000.00 | 显式附注，非TDX独立字段 |

两项受限额均为银行承兑汇票质押的大额存单，本期为2026年6月30日，未误取2025年末比较列。第87页2,791,194,260.28元为包括一年内到期部分的合计，扣除1,823,720,958.90元才是非流动余额，不能重复加回。FN431源倍率10000；两项PDF总额均编码回相同TDX float32位，标准数不恢复PDF小数。

已完成主库副本真实链：现有TDX标准事实→生产`import-reviewed-document`绑定实际镜像归档→`import-supplements`导入4项总额/受限额→标准导出→统一`tools.company_valuation`→共享引擎。归档、补充导入各自重放不新增；Go回归关闭重开验证。工具拒绝覆盖已选原文或改写审核，原始镜像和审核JSON分开归档，公告身份仍属CNINFO，实际获取来源为新浪。未引入新表或依赖。

固定财务/股本基期2026-06-30，信息截止2026-09-17T16:50:00Z，审核有效至2026-10-17T16:50:00Z。WACC显式固定原基线5.7531403615%，不是当日市场参数；增长、利润率、税率、再投资及原桥接不变。扣除7.7亿元受限额后的标准资产共2,021.19424125百万元，按回收率1计入账面代理，股数815.8805376百万，条件值**43.2526126801→45.7299290863元/股**，差额2.4773164061元。全部经营预测、FCFF和经营价值逐项不变，独立Decimal复算通过；不是当前目标价或全部金融资产均已审核。受限资产暂不计值不代表永久损失，也未假设未来质押释放；票据、债务和经营资本仍沿用原政策，没有抵销一次后再释放一次。

[验收回执](acceptance.json)保存真实CLI的run ID和副本范围；[归档审核](document-review.json)与[补充输入](supplements.json)可重放，前后请求以gzip冻结并纳入backend pytest。镜像来源和审核记录以`document_provenance`随事实及补充导出；早于该审核的信息截止拒绝使用这一导出，不声称下载前已取得证据。主库副本位于`workspace/supor-reviewed-assets-20260917/acceptance.duckdb`，原始归档在同目录`raw/`，该轮不是主库发布；9月18日后续发布记录见上方链接，不扩大市场覆盖分母。

```bash
./alphalake import-reviewed-document <db> <raw-root> valuation/research/reviewed-assets-20260917/supor/document-review.json valuation/research/reviewed-assets-20260917/supor/12563787.pdf
./alphalake import-supplements <db> valuation/research/reviewed-assets-20260917/supor/supplements.json
```

上述导入要求库中已存在完全匹配且已解析的CNINFO公告身份，不能用审核文件凭空创建证券或公告。仅填补空原文；拒绝覆盖已有不同PDF、改写既有审核及冒称CNINFO直接下载。

```bash
PYTHONPATH=valuation/backend python -m tools.verify_supor_reviewed_assets
PYTHONPATH=valuation/backend python -m pytest -q valuation/backend/tests/test_method_closure.py -k supor_restricted_asset_evidence
```

校验使用现有pypdf：5项原文金额、2项源位、合计恒等式，以及逐项增加0.01元的5路拒绝。测试纳入现有backend pytest，因此随CI全套运行；本地相关Python105项通过，无skip（2条既有依赖弃用warning），Go全套测试及构建通过。主库/副本另核对383条事实（仅文档元数据不同）和全部80个窗口一致；原9月17日核对时主库无新增补充、当期PDF哈希为空；9月18日已正式补齐。镜像身份与分类仍依赖语义审核，哈希/恒等式不构成另一条独立证据链。
