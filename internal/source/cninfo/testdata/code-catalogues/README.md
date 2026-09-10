# 六位代码检索的真实响应

使用 `new/hisAnnouncement/query`，`searchkey` 分别为300124、603288，其余为生产默认参数：pageNum=1/pageSize=30、column=szse、全部八个定期报告分类、seDate=2025-04-01~2026-09-10、sortName=time/sortType=desc。两次都返回11条，全部属于所查询证券。压缩文件保留响应字节，`hashes.json` 为解压后SHA。

离线生产客户端回放锁定实际请求参数、响应身份/数量，非法代码不能发出HTTP请求。代码检索是显式补采入口，不是交易所名册或全市场覆盖证明；它沿用分页完整性检查，出现其他证券不会静默接受。按代码与全市场窗口使用独立检查点及源定位符。

```bash
go test ./internal/source/cninfo -run TestCodeCatalogueRealResponses -count=1
```
