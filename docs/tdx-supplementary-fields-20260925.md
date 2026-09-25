# TDX完整目录复核与补充标准指标

这是源维护记录；表内源位置用于审核追溯，不是业务查询参数。

2026-09-25重新取得[官方量化目录](https://help.tdx.com.cn/quant/docs/markdown/TdxQuant.md/mindoc-1h10m001ic888.html)，438条定义逐项与本地一致，没有漏编号。原始页面、取得时间和SHA256保存在 `internal/source/tdx/financial/testdata/official-financial-fields-20260925.*`。另一官方专业财务目录补足其未列项目，合计584位置、461官方定义、1参考定义、122未公布语义。

此前“只补三张表”错误地阻断了已定义补充指标。当前schema51由同一完整目录生成新增标准映射：281→346，共新增65项。三表范围不变，补充指标不强塞三表。标准映射率346/462=74.89%，不是公司披露率、估值准入率或全市场原文核验率。源解析位置覆盖584/584，不等于584项都有公开定义。

同时纠正自由流通股的既有单位错误：官方明确为股，原目录误标百分比。原始位和值完全不改，纠正源语义并建立股数标准映射。

新增项包括来源报告EBIT、EBITDA、扣非净利润、每股数据、股本/持有人数量及11项来源TTM。金额为元、股数为股、计数为count，来源万元/万股倍率保留。来源TTM落库为独立`ttm`期间及`TTM`类型，不伪装成年累计，也不进入本地TTM聚合；每股及计数同样不参加普通流量聚合。十大流通股东A股合计仅自2019H1可映射，官方注明此前季度可能包含B/H股，旧期保持源观察。

## EBIT的已知组成边界

官方定义支持接入`reported_ebit`、`reported_ebitda`，不需要另造营业利润代理。真实安克2025H1、2025年、2026H1样本中：

- 来源报告EBIT在源float32 ULP界限内等于利润总额减利息收入，而非利润总额加利息费用减利息收入。
- 来源报告EBITDA减EBIT与当期折旧、无形摊销、长期待摊摊销之和在源精度内相符。使用权折旧没有借此自动加回。
- 普通季报不能直接复用上述组成恒等式，部分季度来源EBITDA为零并按缺失拒绝；不能把三期样本发现推广为供应商全期间公式。
- 因此作为报表期累计的**供应商指标**接入，保留`reported_`名称及组成差异；上述恒等式只是固定样本一致性，不宣称供应商公布公式，也不是独立PDF核验。
- SQLite的EBIT/EBITDA列可从这两项标准累计值导出，季度完整差分，缺前期不补零。但这些值尚不能被认证为达摩达兰调整后经营收益；原生估值的语义阻断继续保留，不因填列而自动放行。

## 本轮新增映射逐项表

| 源位置 | 通用名称 | 标准单位 | 期间 | 源倍率 |
| --- | --- | --- | --- | ---: |
| 2 | `adjusted_earnings_per_share` | CNY/share | ytd | 1 |
| 3 | `retained_earnings_per_share` | CNY/share | instant | 1 |
| 4 | `net_assets_per_share` | CNY/share | instant | 1 |
| 5 | `capital_reserve_per_share` | CNY/share | instant | 1 |
| 7 | `operating_cash_flow_per_share` | CNY/share | ytd | 1 |
| 206 | `reported_adjusted_net_income` | CNY | ytd | 1 |
| 207 | `reported_ebit` | CNY | ytd | 1 |
| 208 | `reported_ebitda` | CNY | ytd | 1 |
| 219 | `reported_operating_cashflow_per_share` | CNY/share | ytd | 1 |
| 225 | `reported_net_cashflow_per_share` | CNY/share | ytd | 1 |
| 239 | `listed_a_shares` | share | instant | 1 |
| 240 | `listed_b_shares` | share | instant | 1 |
| 241 | `listed_h_shares` | share | instant | 1 |
| 242 | `shareholder_count` | count | instant | 1 |
| 243 | `largest_shareholder_shares` | share | instant | 1 |
| 244 | `top_ten_tradable_shareholder_shares` | share | instant | 1 |
| 245 | `top_ten_shareholder_shares` | share | instant | 1 |
| 246 | `institutional_holder_count` | count | instant | 1 |
| 247 | `institutional_shares` | share | instant | 1 |
| 248 | `qfii_holder_count` | count | instant | 1 |
| 249 | `qfii_shares` | share | instant | 1 |
| 250 | `securities_firm_holder_count` | count | instant | 1 |
| 251 | `securities_firm_shares` | share | instant | 1 |
| 252 | `insurance_holder_count` | count | instant | 1 |
| 253 | `insurance_shares` | share | instant | 1 |
| 254 | `fund_holder_count` | count | instant | 1 |
| 255 | `fund_shares` | share | instant | 1 |
| 256 | `social_security_holder_count` | count | instant | 1 |
| 257 | `social_security_shares` | share | instant | 1 |
| 258 | `private_fund_holder_count` | count | instant | 1 |
| 259 | `private_fund_shares` | share | instant | 1 |
| 260 | `finance_company_holder_count` | count | instant | 1 |
| 261 | `finance_company_shares` | share | instant | 1 |
| 262 | `annuity_holder_count` | count | instant | 1 |
| 263 | `annuity_shares` | share | instant | 1 |
| 264 | `top_ten_tradable_a_shares` | share | instant | 1 |
| 265 | `largest_tradable_shareholder_shares` | share | instant | 1 |
| 266 | `free_float_a_shares` | share | instant | 1 |
| 267 | `restricted_tradable_a_shares` | share | instant | 1 |
| 272 | `bank_holder_count` | count | instant | 1 |
| 273 | `bank_shares` | share | instant | 1 |
| 274 | `corporate_holder_count` | count | instant | 1 |
| 275 | `corporate_shares` | share | instant | 1 |
| 276 | `reported_ttm_net_income` | CNY | ttm | 1 |
| 277 | `trust_holder_count` | count | instant | 1 |
| 278 | `trust_shares` | share | instant | 1 |
| 279 | `special_legal_entity_holder_count` | count | instant | 1 |
| 280 | `special_legal_entity_shares` | share | instant | 1 |
| 282 | `adjusted_earnings_per_share_quarter` | CNY/share | quarter | 1 |
| 283 | `reported_ttm_revenue` | CNY | ttm | 10000 |
| 284 | `national_team_shares` | share | instant | 10000 |
| 307 | `reported_ttm_operating_cash_flow` | CNY | ttm | 1 |
| 308 | `reported_ttm_parent_net_income` | CNY | ttm | 10000 |
| 309 | `reported_ttm_adjusted_net_income` | CNY | ttm | 10000 |
| 310 | `reported_ttm_net_cash_increase` | CNY | ttm | 10000 |
| 316 | `reported_ttm_investing_cash_flow` | CNY | ttm | 10000 |
| 319 | `reported_ttm_total_operating_revenue` | CNY | ttm | 10000 |
| 320 | `employee_count` | count | instant | 1 |
| 323 | `reported_ttm_operating_profit` | CNY | ttm | 10000 |
| 325 | `northbound_holder_count` | count | instant | 1 |
| 326 | `northbound_shares` | share | instant | 1 |
| 338 | `reported_ttm_cost_of_revenue_nonfinancial` | CNY | ttm | 10000 |
| 339 | `reported_ttm_cost_of_revenue_financial` | CNY | ttm | 10000 |
| 360 | `asset_management_plan_holder_count` | count | instant | 1 |
| 361 | `asset_management_plan_shares` | share | instant | 1 |

## 剩余已命名位置：逐项处置

以下全部保留原位、版本和源值；不是“TDX没有”。日期/审计编码属于元数据；预测/快报不能用包报告期冒充目标期间；比率有些缺明确单位或窗口；两个贷款位置有同名歧义。不能把这些内容塞进可加总财报金额。

- `ambiguous_source_variant`：2项。
- `definition_requires_review`：1项。
- `event_metadata_not_financial_amount`：4项。
- `forecast_or_preliminary_target_period_unresolved`：36项。
- `period_requires_review`：38项。
- `unit_requires_review`：35项。

| 源位置 | 通用名称 | 保留原因 |
| --- | --- | --- |
| 6 | `reported_return_on_equity` | `unit_requires_review` |
| 71 | `nonrecurring_operating_income_adjustment` | `period_requires_review` |
| 159 | `reported_current_ratio` | `unit_requires_review` |
| 160 | `reported_quick_ratio` | `unit_requires_review` |
| 161 | `reported_cash_ratio` | `period_requires_review` |
| 162 | `reported_interest_coverage` | `unit_requires_review` |
| 163 | `reported_noncurrent_liability_ratio` | `period_requires_review` |
| 164 | `reported_current_liability_ratio` | `period_requires_review` |
| 165 | `reported_cash_to_maturing_debt_ratio` | `definition_requires_review` |
| 166 | `reported_debt_to_tangible_net_assets` | `period_requires_review` |
| 167 | `reported_equity_multiplier` | `period_requires_review` |
| 168 | `reported_equity_to_liabilities` | `period_requires_review` |
| 169 | `reported_tangible_assets_to_liabilities` | `period_requires_review` |
| 170 | `reported_operating_cashflow_to_liabilities` | `period_requires_review` |
| 171 | `reported_ebitda_to_liabilities` | `period_requires_review` |
| 172 | `reported_receivables_turnover` | `unit_requires_review` |
| 173 | `reported_inventory_turnover` | `unit_requires_review` |
| 174 | `reported_working_capital_turnover` | `unit_requires_review` |
| 175 | `reported_asset_turnover` | `unit_requires_review` |
| 176 | `reported_fixed_asset_turnover` | `unit_requires_review` |
| 177 | `reported_receivables_days` | `period_requires_review` |
| 178 | `reported_inventory_days` | `period_requires_review` |
| 179 | `reported_current_asset_turnover` | `unit_requires_review` |
| 180 | `reported_current_asset_days` | `period_requires_review` |
| 181 | `reported_asset_days` | `period_requires_review` |
| 182 | `reported_equity_turnover` | `unit_requires_review` |
| 183 | `reported_revenue_growth` | `period_requires_review` |
| 184 | `reported_net_income_growth` | `period_requires_review` |
| 185 | `reported_net_assets_growth` | `period_requires_review` |
| 186 | `reported_fixed_assets_growth` | `period_requires_review` |
| 187 | `reported_total_assets_growth` | `period_requires_review` |
| 188 | `reported_investment_income_growth` | `period_requires_review` |
| 189 | `reported_operating_profit_growth` | `period_requires_review` |
| 190 | `reported_adjusted_eps_growth` | `period_requires_review` |
| 191 | `reported_adjusted_net_income_growth` | `period_requires_review` |
| 193 | `reported_profit_to_cost_ratio` | `period_requires_review` |
| 194 | `reported_operating_margin` | `unit_requires_review` |
| 195 | `reported_revenue_tax_ratio` | `unit_requires_review` |
| 196 | `reported_cost_to_revenue` | `unit_requires_review` |
| 197 | `reported_return_on_equity_average` | `unit_requires_review` |
| 198 | `reported_investment_return` | `unit_requires_review` |
| 199 | `reported_net_sales_margin` | `period_requires_review` |
| 200 | `reported_return_on_assets` | `unit_requires_review` |
| 201 | `reported_net_profit_margin` | `unit_requires_review` |
| 202 | `reported_gross_margin` | `period_requires_review` |
| 203 | `reported_three_expense_ratio` | `unit_requires_review` |
| 204 | `reported_administrative_expense_ratio` | `unit_requires_review` |
| 205 | `reported_finance_cost_ratio` | `unit_requires_review` |
| 209 | `reported_ebitda_margin` | `period_requires_review` |
| 210 | `reported_debt_to_assets` | `period_requires_review` |
| 211 | `reported_current_assets_ratio` | `unit_requires_review` |
| 212 | `reported_monetary_funds_ratio` | `unit_requires_review` |
| 213 | `reported_inventory_ratio` | `unit_requires_review` |
| 214 | `reported_fixed_assets_ratio` | `unit_requires_review` |
| 215 | `reported_liability_structure_ratio` | `unit_requires_review` |
| 216 | `reported_parent_equity_to_invested_capital` | `period_requires_review` |
| 217 | `reported_equity_to_interest_bearing_debt` | `period_requires_review` |
| 218 | `reported_tangible_assets_to_net_debt` | `period_requires_review` |
| 220 | `reported_revenue_cash_content` | `period_requires_review` |
| 221 | `reported_operating_cashflow_to_operating_income` | `period_requires_review` |
| 222 | `reported_customer_cash_to_revenue` | `period_requires_review` |
| 223 | `reported_operating_cashflow_to_revenue` | `unit_requires_review` |
| 224 | `reported_capex_to_depreciation_amortization` | `unit_requires_review` |
| 226 | `reported_operating_cashflow_to_short_term_debt` | `unit_requires_review` |
| 227 | `reported_operating_cashflow_to_total_debt` | `unit_requires_review` |
| 228 | `reported_operating_cashflow_to_net_income` | `unit_requires_review` |
| 229 | `reported_cash_return_on_assets` | `unit_requires_review` |
| 281 | `reported_weighted_return_on_equity` | `unit_requires_review` |
| 285 | `forecast_parent_net_income_growth_lower` | `forecast_or_preliminary_target_period_unresolved` |
| 286 | `forecast_parent_net_income_growth_upper` | `forecast_or_preliminary_target_period_unresolved` |
| 287 | `preliminary_parent_net_income` | `forecast_or_preliminary_target_period_unresolved` |
| 288 | `preliminary_adjusted_net_income` | `forecast_or_preliminary_target_period_unresolved` |
| 289 | `preliminary_total_assets` | `forecast_or_preliminary_target_period_unresolved` |
| 290 | `preliminary_net_assets` | `forecast_or_preliminary_target_period_unresolved` |
| 291 | `preliminary_earnings_per_share` | `forecast_or_preliminary_target_period_unresolved` |
| 292 | `preliminary_diluted_return_on_equity` | `forecast_or_preliminary_target_period_unresolved` |
| 293 | `preliminary_weighted_return_on_equity` | `forecast_or_preliminary_target_period_unresolved` |
| 294 | `preliminary_net_assets_per_share` | `forecast_or_preliminary_target_period_unresolved` |
| 313 | `forecast_announcement_date` | `event_metadata_not_financial_amount` |
| 314 | `financial_report_announcement_date` | `event_metadata_not_financial_amount` |
| 315 | `preliminary_announcement_date` | `event_metadata_not_financial_amount` |
| 317 | `forecast_parent_net_income_lower` | `forecast_or_preliminary_target_period_unresolved` |
| 318 | `forecast_parent_net_income_upper` | `forecast_or_preliminary_target_period_unresolved` |
| 321 | `reported_fcff_per_share` | `period_requires_review` |
| 322 | `reported_fcfe_per_share` | `period_requires_review` |
| 327 | `reported_interest_bearing_debt_ratio` | `unit_requires_review` |
| 329 | `reported_return_on_invested_capital` | `unit_requires_review` |
| 330 | `preliminary_revenue_current` | `forecast_or_preliminary_target_period_unresolved` |
| 331 | `preliminary_revenue_prior` | `forecast_or_preliminary_target_period_unresolved` |
| 332 | `preliminary_operating_profit_current` | `forecast_or_preliminary_target_period_unresolved` |
| 333 | `preliminary_operating_profit_prior` | `forecast_or_preliminary_target_period_unresolved` |
| 334 | `preliminary_pretax_profit_current` | `forecast_or_preliminary_target_period_unresolved` |
| 335 | `preliminary_pretax_profit_prior` | `forecast_or_preliminary_target_period_unresolved` |
| 336 | `audit_opinion_code` | `event_metadata_not_financial_amount` |
| 337 | `reported_dividend_payout_ratio` | `period_requires_review` |
| 340 | `forecast_adjusted_net_income_lower` | `forecast_or_preliminary_target_period_unresolved` |
| 341 | `forecast_adjusted_net_income_upper` | `forecast_or_preliminary_target_period_unresolved` |
| 342 | `forecast_adjusted_net_income_growth_lower` | `forecast_or_preliminary_target_period_unresolved` |
| 343 | `forecast_adjusted_net_income_growth_upper` | `forecast_or_preliminary_target_period_unresolved` |
| 344 | `forecast_basic_eps_lower` | `forecast_or_preliminary_target_period_unresolved` |
| 345 | `forecast_basic_eps_upper` | `forecast_or_preliminary_target_period_unresolved` |
| 346 | `forecast_basic_eps_growth_lower` | `forecast_or_preliminary_target_period_unresolved` |
| 347 | `forecast_basic_eps_growth_upper` | `forecast_or_preliminary_target_period_unresolved` |
| 348 | `forecast_adjusted_eps_lower` | `forecast_or_preliminary_target_period_unresolved` |
| 349 | `forecast_adjusted_eps_upper` | `forecast_or_preliminary_target_period_unresolved` |
| 350 | `forecast_adjusted_eps_growth_lower` | `forecast_or_preliminary_target_period_unresolved` |
| 351 | `forecast_adjusted_eps_growth_upper` | `forecast_or_preliminary_target_period_unresolved` |
| 352 | `forecast_revenue_lower` | `forecast_or_preliminary_target_period_unresolved` |
| 353 | `forecast_revenue_upper` | `forecast_or_preliminary_target_period_unresolved` |
| 354 | `forecast_revenue_growth_lower` | `forecast_or_preliminary_target_period_unresolved` |
| 355 | `forecast_revenue_growth_upper` | `forecast_or_preliminary_target_period_unresolved` |
| 356 | `forecast_revenue_after_deductions_lower` | `forecast_or_preliminary_target_period_unresolved` |
| 357 | `forecast_revenue_after_deductions_upper` | `forecast_or_preliminary_target_period_unresolved` |
| 362 | `reported_financial_score` | `period_requires_review` |
| 440 | `financial_loans_and_advances` | `ambiguous_source_variant` |
| 453 | `financial_loans_and_advances` | `ambiguous_source_variant` |

### 剩余项目的补证路径

35项单位问题需官方比率单位/公式或真实响应与可比报表分量证明比例尺度；38项期间问题需明确计算窗口、年化及分母时点，不能只看包报告期。36项预测/快报需绑定目标报告期（公告日期不能替代），优先查供应商对应元数据；若只能依公告补目标期，再单独评估接入。两个贷款位置需官方变体说明或真实报表组成消歧；仅参考定义的一项等权威依据。四个事件/审计编码已保留源元数据，原本就不应变成财务金额。122个未公布位置继续无损同步，不猜名称。

这些约束不阻止已明确字段落库，也不要求所有已定义字段逐公司PDF核验。本轮未增加其他数值来源或估值代理。

## 同步、转换与验证

新建库自动具备完整映射；既有schema50须先备份，再显式运行`alphalake upgrade-financial-catalog <db>`，普通初始化不静默迁移。转换事务保留原始源、公告、事实和审核记录，只更新目录并扩展TTM期间；然后执行`materialize-fundamentals`从已同步原始值补出标准事实。后续`sync-financial <db> --latest N`仍按完整包同步所有位置，不新增估值白名单。同步源包不代表已经关联全部历史公告。

真实回归复用安克、茅台六期归档，65项共780个候选，488个非零事实、292个歧义零拒绝；逐源位和倍率对账，旧事实不变，错误倍率撤除、恢复、重开幂等通过。来源TTM、计数和每股不得进入派生TTM有独立断言。官方新旧页面均逐条检查标签和哈希。源EBIT组成差异也已锁定回归。

主库首次两个默认全字段运行5439/5440在合并阶段触及DuckDB 256MiB查询上限，均保留失败运行；1GiB独立服务保护有效，无系统OOM。只读复核原4,403,786事实未变，19,310,544源记录行数及内容XOR摘要一致，事务回滚成立。单字段诊断5441新增31,982条来源报告EBIT、591个歧义零拒绝；这不是整批验收。随后将每批六字段缩至三字段，并限定合并查询范围，继续保持一事务、一个运行，不按批提交。三字段接入运行5442仍在股东数据批次触及256MiB上限并回滚；因此改用384MiB查询预算验证，服务硬上限仍为1GiB、单线程、禁用swap。不能把缩批次本身声称为问题已解决。

384MiB下运行5443仍在来源TTM批次触及上限并回滚。最终512MiB查询预算、1GiB服务硬限额下，运行5444一次事务完成65项：2,117,245候选、1,223,521可物化、893,724拒绝；扣除单字段诊断已插入31,982条，本次新增1,191,539，更新/删除均零。

重开后默认全字段运行5445通过：11,269,688候选、5,627,307可物化、5,642,381拒绝，新增/更新/删除全零。旧4,403,786事实按ID逐行哈希比较一致；19,310,544源记录的行数及整行XOR摘要一致。这是内容比较，不是独立财务语义核验。

新增事实证券并集5,540个；2026H1为5,527个。范围为本地主库2024Q1—2026H1已关联报告，2024各期仅一个证券，不能称全市场十年历史齐全。尚有419条待关联、74条歧义关联；原始同步33,066个报告记录×584位置完整保留。逐期计数和运行记录见[机器收据](acceptance/tdx-supplementary-20260925.json)。后续实时同步与SQLite导出另行记录。


## 下载诊断与最终节点策略

实时同步暴露两层问题：行情节点对财务文件返回空响应（每个请求最多三台不同服务器），官方8001端口的整包HTTP连接又在约1.84MB后中断。运行5446明确失败、未发布财务数据；首次分段尝试5447仍遇到单段连接截断，也保留失败记录。80和443端口实测拒绝连接，不改换第三方数值源。

256KiB分段试验在运行5448的2.88MB位置连续三次超时；64KiB试验最终取得完整5,751,738字节包（SHA256 `388a2ba8c65b40a687a26f9244ac8c616d99522efa1bc9e39df16aa935da7dee`），留作本轮取证归档。独立curl整包对照也在120秒后超时，仅收到730,972字节；不能据此确定问题在服务器还是网络线路。

按用户后续明确约束，**正式实现撤除上述HTTP分段及原HTTP后备**，直接使用`injoyai/tdx.Hosts`完整候选池及上游顺序，复用既有每请求最多三台的轮询。成功节点复用，失败关闭并推进；失败记录保留，不使用另行探测的地址。新建Client不持久化上一进程的节点位置。试验归档不冒充正式节点策略的实网成功。

该包随后在原始入库阶段触发`alphalake-range64.service`的cgroup OOM（2026-09-25T07:46:31Z）；1GiB硬限额、禁用swap保持，宿主机与终端未受波及。期间896MiB软限额持续回收，改为与硬限额相同后服务仍触顶，故不能再提高预算盲目重跑。通过正常打开数据库回放WAL恢复；最新包不计入同步完成。源入库的整包内存峰值是独立于标准物化批次的未解决问题。

数据库备份已按重开验收结果轮转，释放2,374,799,360字节；保留本轮转换前最近备份。逐路径哈希和理由见[清理收据](acceptance/workspace-cleanup-20260925.json)。


## SQLite实际导出

目录发布后已从主库导出安克、茅台、苏泊尔三家公司（2026H1、信息截止2026-09-25T00:00:00Z），共3,549条标准事实；快照路径`workspace/sqlite-export-20260925/valuation.sqlite`。2025年度EBIT/EBITDA已非空，季度EBIT按累计差分；季度EBITDA因一季报来源零歧义不能差分，继续留空。

以下为历史验收：十个年度、八个季度窗口下，收入/EBIT缺失单元格分别为安克19、茅台22、苏泊尔22；当时三家公司均被旧门槛阻断。窗口缺项不等于原模型必需项，当前已[按实际消费纠正准入](valuation-input-gates-20260925.md)。11个目标字段有直接映射，不等于所有历史单元格齐全，更不等于原生估值已通过口径审核。具体数值、缺项及快照哈希见[SQLite收据](acceptance/tdx-supplementary-sqlite-20260925.json)。当时8080网页使用的09-22快照未替换；当前已替换为09-25快照，前端未修改。


## 最终验收边界

最终上游hosts策略已实测41个不同节点，14次请求、42次节点尝试（最后回绕首节点一次），40次返回空财务文件、2次连接超时，未取得有效清单。查询确认原始事实仍19,310,544条、标准事实仍5,627,307条，最新包MD5没有推进完成检查点；运行5449已依据服务OOM日志由running明确收口为failed。见[节点及恢复收据](acceptance/tdx-upstream-hosts-20260925.json)。不能称最新包入库、全历史同步或原生估值已完成。

最终代码的Go全套回归及CLI构建通过；Python3.12导出/网页数据/集成相关91项通过（非Python全套）。新增目录及真实样本回归在Go常规测试内；既有可选外部PDF、Python参考源验收的环境门槛未改变。格式、文档链接与`git diff --check`通过，零依赖变更。
