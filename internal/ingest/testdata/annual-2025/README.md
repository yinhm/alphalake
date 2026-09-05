# 2025 年报真实样本

采集日期：2026-09-05。`values.csv` 是从 CNINFO 年报「分季度主要财务指标」第四季度栏独立核对的 16 个金额，覆盖山金国际、大为股份、大金重工、德赛西威；页码为 PDF 文件从 1 开始的物理页码。单位均为人民币元，期间为 2025Q4。

`gpcw20251231.zip` 来自 TDX 官方 `http://down.tdx.com.cn:8001/tdxfin/gpcw20251231.zip` 的当次归档，原包 SHA-256 为 `d0428e570d81d68315acafc6ba406d87429035e34369db66464abebdbd300bf8`，大小 5,799,231 字节，含 5,558 家公司、每家 584 个字段。

测试包只保留上述四家公司及 603007、603659 的完整记录，重写头部记录数、索引偏移并重新压缩；代码、市场标记及每条记录的 2,336 字节财务字段不变。它是裁剪样本，不能冒充上游完整包或用于整包完成检查点。测试包 SHA-256：`65dce4ff53903ee49e5abe17e106faeebcd5a8e1419a7d2a0f9f3c3abfafd0ce`。

在仓库根目录运行：

```bash
go test ./internal/ingest -run '^TestRealAnnualReportValues$' -count=1 -v
```

测试直接解析真实字段字节，将年报十进制金额转换为 float32 后逐位比较，包含负利润和负现金流。CSV 保留原文 URL、PDF SHA-256 和页码，PDF 大文件保留在本地原始归档中，不随代码提交。它不是通用 PDF 提取器；增加样本时必须重新核对原文、期间和单位。

该样本证明所选数值与对应年报一致，不证明全市场覆盖或完整历史版本；9 月取得的财务包不能单凭公告日期证明其全部数值在 3 月已存在。

## 采集链路重放

`page-1.json` 至 `page-3.json` 是 2026-09-05 06:49:52 UTC 采集的原始 CNINFO 响应，查询公告日为 2026-03-06、每页 5 条；保留上游错误的 `totalpages=2`，实际共 12 条，需要三页。三个文件的 SHA-256 依次为：

- `9c67bd3d55f96d4bca03dbe7981fa4529147677a22eabdd79780c28f2781b169`
- `6b694d34ac13f3523cbeff9e5d8b1be9b442d5609dc1c1f2acf282e8435d0dd9`
- `710a794951e1c77a9ca804ef4f63916bf99ead09b4cb3b297f8e4233603a0b2c`

`instruments.json` 是同次真实 TDX 主数据的七证券切片，空有效期不构成已审核的历史生命周期证据。`documents.json` 记录六份原文的 URL、归档相对路径、大小和 SHA-256。

```bash
# 默认离线运行：真实目录字节和财务记录，无需本地历史归档。
go test ./internal/ingest -run '^TestReal(AnnualReportValues|FinancialWorkflow)$' -count=1 -v

# 同时检查真实 PDF 完整性、下载入库、正文复用及不同模式的检查点。
ALPHALAKE_ACCEPTANCE_RAW="$PWD/workspace/live-sync-20260905/raw" \
  go test ./internal/ingest -run '^TestRealFinancialWorkflow$' -count=1 -v
```

测试在临时数据库中调用生产 HTTP 客户端和采集/归一化/物化函数，向第二页注入 HTTP 503，关闭并重新打开数据库后恢复，再检查幂等重放和 PIT 边界。未设置环境变量时明确记录未校验正文；设置后缺失或损坏的 PDF 必须失败。测试不修改原始归档，不访问上游，不依赖旧数据库及旧运行日志。
