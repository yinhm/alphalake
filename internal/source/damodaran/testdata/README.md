# 达摩达兰国家风险真实归档

- 作者／credit：Aswath Damodaran，NYU Stern。原始数据及说明来自其公开网站；沿用 valuation 项目中已有的工作簿解析器，不改变原代码 credit。
- 来源：https://pages.stern.nyu.edu/~adamodar/pc/datasets/ctrypremJuly26.xlsx
- 本次获取：2026-09-09；SHA-256：`8d7237c432bca23cd680f149395aa518a465d0b77bec1fdf788edc7a1748c60a`。
- 完整工作簿约 372 KiB，未裁剪。`ERPs by country!B2` 标记 2026-07-01，C2 另有 7 月 9 日更正说明；两者均不作为已核实的公开可用时间。
- `expected.json` 为 10 项冻结解析预期。runtime 是生成环境记录，测试允许它变化，但实际发布签名必须包含运行环境。

第一批仅发布 CN、HK、US 的 D/E/F 三列和 E3 成熟市场 ERP。CN/HK 使用评级法的违约利差、总 ERP 和 CRP；美国行原值保留，不强行用成熟市场 ERP 加 CRP 重建总 ERP。G/H/I 的 CDS 路径不在本批范围，不能与评级法互换。

标准值为小数比例，按 Decimal half-even 保留 12 位。`raw_value` 是 openpyxl 解码后的数值表示，原始 Excel 数字文本与二进制仍在归档中；不声称它就是原 XML 字符串。独立 XML 测试不调用原解析器提取期望数值，逐单元格复核量化后的 10 项；哈希用于归档身份，不替代语义审核。

验证：

```bash
go test ./internal/source/damodaran ./internal/store/duckdb -run 'TestSelectedCountryContract|TestCountryRiskPublication' -count=1
ALPHALAKE_TEST_PYTHON=python go test ./internal/ingest -run TestCountryRiskRealArchiveReplay -count=1
cd valuation/backend
python -m pytest -q tests/test_damodaran_country_snapshot.py
```

后两项需要已安装 valuation Python 依赖；CI 在安装后显式运行。Go 集成测试使用 httptest 本地服务提供此完整工作簿，涵盖首次发布、在线重复获取、畸形新文件拒绝及随后离线重放；离线 HTTP 计数不增加，重开数据库后失败运行仍存在。
