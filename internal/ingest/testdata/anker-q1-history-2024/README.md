# 安克2024Q1收入补链

目的：2025H1历史增长规则需要至少两组同季同比，原库只有2024Q2比较季，补入2024Q1，保留原准入要求。

[CNINFO一季报](https://static.cninfo.com.cn/finalpage/2024-04-27/1219865739.PDF)第11页“2、合并利润表”，人民币元、本期/上期列，营业收入4,377,729,053.27元；TDX FN230为4,377,729,024元（bits=1333950316），按源精度一致。比较列3,365,103,838.16元仅核对印刷值，不当作TDX标准事实。匹配只去排版空白，固定章节和列序，不做模糊标签搜索。

原始目录、请求、PDF及哈希保留；目录仅一条但上游`totalpages=0`原样存档。FN314=240427与公告日期一致，日期精度可用边界为2024-04-28中国零点。取得时间为后来时点，不冒称严格历史PIT。

裁剪包保存安克整条584字段记录，`packages.json`保留全包SHA/MD5与裁剪记录SHA。全包逐字节核对已执行；日常离线CI使用裁剪包和PDF，不要求workspace全包存续。

```bash
PYTHONPATH=valuation/backend python -m tools.verify_anker_q1_history internal/ingest/testdata/anker-q1-history-2024
PYTHONPATH=valuation/backend python -m pytest valuation/backend/tests/test_anker_q1_history.py -q
go test ./internal/ingest -run '^TestRealAnkerQ1History$' -count=1
```

PDF重提取与金额/源位/身份篡改拒绝进入既有pytest CI，依赖已有pypdf；Go测试覆盖真实导入、标准FN230位值/期间/单位、公告边界、重开重放以及当前估值输入不变。其他整条记录字段依已有审核映射物化，不声称本样本逐项核验全部584字段。
