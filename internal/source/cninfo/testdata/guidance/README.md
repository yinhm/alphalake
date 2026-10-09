# 巨潮业绩预告真实目录响应

2026-10-09从 `https://www.cninfo.com.cn/new/hisAnnouncement/query` POST取得；类别 `category_yjygjxz_szsh`，同时含预告、预增、快报及回复类。原始字节不修改。

- `page1.json`／`page2.json`：2026-09-01～10-09、每页10条，实际10+6=16，totalpages=1错误。用记录数及去重判断完整，不以16当全市场预告公司数。
- `dense/page1.json`～`page5.json`：2026-07-11同日125条，pageSize=30、sortName和sortType为空；实际30+30+30+30+5、ID无重复。该范围主要为H1预告，不将其当Q3输入。
- `dense/unstable-page3.json`／`unstable-page4.json`：同一单日、显式sortName=time和sortType=desc时的真实第3／4页。维科技术1225418334跨页重复，足以拒绝当前分页集合，不据重复哈希认证完整。

服务没有上游快照令牌；默认排序实测完整不等于永久稳定，数量变化与重叠仍核验并有限拆分。测试以httptest重放目录；PDF失败恢复用明确合成附件，实际18份PDF入库验收另在workspace留证，不增加日常触网或全PDF测试。

| 文件 | SHA-256 |
|---|---|
| `dense/page1.json` | `f5bb01dc3b4c525d94a594bd02e9ab509e9c71e76ce8a712d8f7985c69521e26` |
| `dense/page2.json` | `c68d15b76aba54ba3fac817b57472077318f3ace7204a0f729ed1bafaa907ffe` |
| `dense/page3.json` | `55531ba26f64b7dfb3f6840e7de430ed8a85548e5851e1321f62155a50e6abc1` |
| `dense/page4.json` | `51ba31547a7acfdb97be4dccdf7ee4d12e1f4eb3014352c906357421cf4b35a7` |
| `dense/page5.json` | `f292efe4e0f8cbe222f564266eb342db65499782d589c4a6d6978a51228beec8` |
| `dense/unstable-page3.json` | `734d507e1a2dddef771a159471e5d8f5b394bdbf359b96054e752f883c4b189b` |
| `dense/unstable-page4.json` | `eb614ac3f36124ffaa7b3295a4bbe55f907aa077c85b38e66e49a3cbd477d5b0` |
| `page1.json` | `74d0e1c1161baf72d91ef935156f3cd3585228455f83c292bd65deacea47e858` |
| `page2.json` | `6be66dba6fef087e6f07ff8fb22cf18998ba593e4a84e12c10dcee41da84b23b` |
