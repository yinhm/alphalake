# 多起点经营利润误差：收入还是利润率

复用固定五家公司、2023/2024/2025H1三个起点和既有一至三年结果，不改预测、不新增样本或参数。45个位置中30个成熟、15个未来，金融兼营信号及可比性未闭合公司仍完整保留。这是已暴露研究样本的解释，不是独立留出或生产估值准入。

结果不支持把“安克最近一年利润率偏差较大”扩展为统一优先项：**一年期两项贡献接近，两至三年收入偏差更大**。安克六个成熟窗口中也有四个收入贡献更大；苏泊尔为五个。没有由单年结果反推公司专属规则。

| 预测期限 | 成熟/全部位置 | 收入贡献较大的位置 | 收入贡献绝对值均值/实际收入 | 利润率贡献绝对值均值/实际收入 | 总利润误差绝对值均值/实际收入 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 一年 | 15/15 | 7 | 0.9150% | 0.9917% | 1.3289% |
| 两年 | 10/15 | 8 | 2.1734% | 1.4838% | 2.4086% |
| 三年 | 5/15 | 4 | 3.5039% | 2.0732% | 2.6845% |

30个成熟位置中14个存在收入与利润率贡献异号。两项绝对值不能直接相加当总误差，小的利润总误差可能来自抵消。上述百分比均以实际收入为分母，不是利润自身的百分比误差，也不是“可消除的误差比例”。期限之间起点集合不同，同公司与目标期重叠，不把期限差异解释为独立统计证据。

## 算式与边界

令预测/实际收入为Rp、Ra，预测/实际利润率为mp、ma，均由既有预测/实际EBIT除以各自收入取得。固定对称分解：

- 收入贡献 = (Rp − Ra) × (mp + ma) / 2。
- 利润率贡献 = (mp − ma) × (Rp + Ra) / 2。
- 二者之和 = 预测EBIT − 实际EBIT。

这是先替收入、先替利润率两个次序的平均算式，避免替换顺序左右结论。它不是独立证据链，也不是成本、产品组合或经营效率的因果识别；源科目与公司业务范围的既有限制仍存在。没有将实际利润率用于起点预测，没有生成新DCF或推断FCFF准确度。

后续优先保持多年收入、利润率与投入资本的联合约束；不能因为这个分解就提高安克增长上限、修改前五年增长，或再扫描已失败的利润率平滑权重。此前的收入候选、历史利润率均值和资本余额候选仍各自保留失败结论。本次纠正研究优先级，不升级任何规则。

## 重放与校验

[完整结果](profit-error-attribution.json)保存45个位置、原始结果文件哈希、逐项贡献及按公司/期限汇总。安克2025H1研究预测与实际另同上轮归档标准链四项金额交叉核对，均在1e-8百万元内一致；这只是一个位置的桥接，不外推其余29个位置已通过标准层。本地已沿原README的生产共享函数从源切片精确重放30个原预测；另用Decimal复算30项分解，给利润率贡献加100万元会被恒等式检查拒绝。未下载数据、未改标准库或生产代码，无需重跑Go全套；本轮没有新增CI测试，下面的独立只读命令可精确复现本结果，既有源/预测回归继续适用。

```bash
python3 - <<'PYTHON'
import gzip, hashlib, json, math
from pathlib import Path
root = Path('valuation/research/continuing-operations-five')
inputs = {name: (root/name).read_bytes() for name in ('baseline.json', 'multiyear-result.json.gz')}
one = json.loads(inputs['baseline.json'])
multi = json.loads(gzip.decompress(inputs['multiyear-result.json.gz']))
rows = []
for origin in one['origins']:
    for row in origin['results']:
        rows.append(row | dict(origin=origin['origin'], horizon=1, target=str(int(origin['origin'][:4])+1)+'-06-30'))
rows.extend(row | (dict(forecast=row['forecasts']['flat_first_five']) if row['status']=='evaluated' else {}) for row in multi['results'])
assert len(rows) == 45 and len({(r['code'],r['origin'],r['horizon']) for r in rows}) == 45
output = []
for r in rows:
    item = {k:r[k] for k in ('code','origin','horizon','target','status')}
    if r['status'] == 'evaluated':
        rp, ep = (r['forecast'][k] for k in ('revenue','ebit'))
        ra, ea = (r['actual'][k] for k in ('revenue','ebit'))
        assert rp > 0 and ra > 0
        mp, ma = ep/rp, ea/ra
        revenue = (rp-ra)*(mp+ma)/2
        margin = (mp-ma)*(rp+ra)/2
        assert math.isclose(revenue+margin,ep-ea,rel_tol=1e-12,abs_tol=1e-8)
        # 两个替换顺序的平均值；与先替收入或先替利润率的路径无关。
        assert math.isclose(revenue, ((rp*mp-ra*mp)+(rp*ma-ra*ma))/2,rel_tol=1e-12,abs_tol=1e-8)
        item.update(predicted_revenue=rp, actual_revenue=ra, predicted_margin=mp, actual_margin=ma,
                    error_million_cny=ep-ea, revenue_contribution_million_cny=revenue,
                    margin_contribution_million_cny=margin, offsetting=revenue*margin<0,
                    revenue_dominates=abs(revenue)>abs(margin))
    output.append(item)
def aggregate(selected):
    valid = [r for r in selected if r['status']=='evaluated']
    return dict(positions=len(selected), evaluated=len(valid),
                revenue_dominates=sum(r['revenue_dominates'] for r in valid),
                offsetting=sum(r['offsetting'] for r in valid),
                mean_abs_revenue_contribution_pct_actual_revenue=sum(abs(r['revenue_contribution_million_cny'])/r['actual_revenue']*100 for r in valid)/len(valid),
                mean_abs_margin_contribution_pct_actual_revenue=sum(abs(r['margin_contribution_million_cny'])/r['actual_revenue']*100 for r in valid)/len(valid),
                mean_abs_total_error_pct_actual_revenue=sum(abs(r['error_million_cny'])/r['actual_revenue']*100 for r in valid)/len(valid))
result = dict(inputs_sha256={k:hashlib.sha256(v).hexdigest() for k,v in inputs.items()},
              method='symmetric_two_factor_identity_not_causal_or_new_forecast', results=output,
              overall=aggregate(output),
              by_horizon={str(h):aggregate([r for r in output if r['horizon']==h]) for h in (1,2,3)},
              by_company={c:aggregate([r for r in output if r['code']==c]) for c in sorted({r['code'] for r in output})},
              boundary='All five preselected companies retained, including financial-operation signals and unresolved comparability; 30 due and 15 future positions; exposed overlapping research windows, not standard admission, new holdout or DCF accuracy; no policy changes')
assert result == json.loads((root/'profit-error-attribution.json').read_bytes())
print('30 decompositions replayed; 15 future positions retained')
PYTHON
```
