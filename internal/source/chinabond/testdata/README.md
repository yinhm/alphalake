# 中债人民币国债收益率真实归档

编制方／credit：中央国债登记结算有限责任公司；[公开页面](https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/more?locale=cn_ZH)。

- `curve.html`：2026-09-09 下载的完整响应，17,742 字节；页面日期也为 2026-09-09。
- SHA-256：`278cf6c29bc44c7cc5f22c9883a4c5ca1f4dab1bbdc6a0fd026033d05ca8e3cd`。
- `expected.json`：八个国债期限点的固定预期；source_locator 指向目标表的国债行和可见单元格，HTML 注释不算单元格。
- 来源百分数除以 100 转为标准小数，十年点为 `1.6816% = 0.016816`；不将其标为已调整的无风险利率。

`TestCurveContract`、`TestBetaYieldAtomicPublication`、`TestBetaYieldRealArchiveReplay` 及 Python `tests/test_beta_yield_reference.py` 锁定正负路径，CI 显式运行需要 Python 的集成测试。公开页面不代表稳定 API 或商业再分发授权；接入范围见[说明](../../../../docs/beta-yield-sync.md)。
