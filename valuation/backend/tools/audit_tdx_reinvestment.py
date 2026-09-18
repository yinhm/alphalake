"""固定样本再投资源分量覆盖核查；不将会计分量可用性冒充历史FCFF闭合。"""
import argparse
from collections import Counter,defaultdict
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from tools.backtest_tdx_history import at,available,value,quarter_periods

BALANCES=('FN11','FN12','FN13','FN17','FN44','FN46','FN47')
AMBIGUOUS=('FN136','FN137','FN138','FN579','FN581','FN146','FN147','FN148','FN104')
SCALES={'FN579':10000,'FN581':10000}
GROUPS={
    'capital_expenditure_cash':('FN114',),
    'reported_operating_cashflow':('FN234',),
    'depreciation_core':('FN136','FN137','FN138'),
    'depreciation_extensions':('FN579','FN581'),
    'cashflow_wc_reconciliation':('FN146','FN147','FN148'),
    'partial_balance_changes':BALANCES,
    'income_tax_accrual':('FN92','FN93'),
    'all_taxes_paid':('FN104',),
}


def component(index,artifacts,code,end,field,cutoff):
    if field in BALANCES:
        periods=[(end.isoformat(),1),(end.replace(year=end.year-1).isoformat(),-1)]
    elif field=='FN234':periods=[(p,1) for p in quarter_periods(end)]
    else:periods=[(end.isoformat(),1),(f'{end.year-1}-12-31',1),(end.replace(year=end.year-1).isoformat(),-1)]
    inputs=[];issues=[];total=Decimal(0)
    for period,coefficient in periods:
        rows=index.get((code,period),[]);item=dict(period=period,coefficient=coefficient,field=field)
        if not rows:reason='missing_record'
        elif len(rows)!=1:reason='duplicate_identity'
        else:
            row=rows[0];item.update(artifact=row['artifact'])
            try:
                artifact=artifacts[row['artifact']]
                if artifact['report_period']!=period:raise ValueError('artifact period differs')
                if available(row,artifact)>at(cutoff):reason='unavailable_at_cutoff'
                elif field not in row['bits']:reason='missing_field'
                else:
                    n=value(row,field);item.update(bits=row['bits'][field],source_value=str(n),multiplier=SCALES.get(field,1))
                    if field in AMBIGUOUS and n==0:reason='source_zero_ambiguous'
                    else:reason=None;total+=n*SCALES.get(field,1)*coefficient
            except (ValueError,KeyError,ArithmeticError) as exc:
                reason='invalid_source';item['detail']=str(exc)
        item['status']=reason or 'available_source_component';inputs.append(item)
        if reason:issues.append(reason)
    if not issues and field=='FN114' and total<0:issues.append('negative_cumulative_capex')
    return dict(status='blocked' if issues else 'available_source_component',issues=sorted(set(issues)),
                value_cny=None if issues else str(total),period_basis='instant_yoy_change' if field in BALANCES else 'ttm',source_inputs=inputs)


def audit(protocol,source):
    if protocol['protocol_id']!='tdx-reinvestment-coverage-v1' or source['contract_version']!='tdx-history-source-v1':raise ValueError('unsupported audit protocol/source')
    rules=protocol['source_rules']
    if rules['multipliers']!=SCALES or set(rules['zero_ambiguous'])!=set(AMBIGUOUS):raise ValueError('source rules differ')
    fields=tuple(f for group in GROUPS.values() for f in group)
    if set(protocol['additional_source_fields'])!={int(f[2:]) for f in fields}:raise ValueError('audit fields differ')
    samples=[s for s in protocol['samples'] if s['split']==protocol['audit_split']]
    if not samples or len({s['code'] for s in samples})!=len(samples):raise ValueError('empty or duplicate sample')
    periods=protocol['periods']
    if len(set(periods))!=len(periods) or any(p[4:]!='-06-30' for p in periods):raise ValueError('unique H1 periods required')
    artifacts={a['file']:a for a in source['artifacts']}
    if len(artifacts)!=len(source['artifacts']):raise ValueError('duplicate artifact')
    index=defaultdict(list)
    for r in source['records']:index[(r['code'],r['period'])].append(r)
    rows=[]
    for s in samples:
        for period in periods:
            parts={f:component(index,artifacts,s['code'],date.fromisoformat(period),f,protocol['evaluation_as_of']) for f in fields}
            groups={g:all(parts[f]['status']=='available_source_component' for f in fs) for g,fs in GROUPS.items()}
            rows.append(dict(code=s['code'],name=s['name'],period=period,stratum=s['stratum'],components=parts,source_groups_available=groups,
                             actual_fcff=None,fcff_status='missing_classification_evidence'))
    def summary(selected):
        return dict(candidates=len(selected),source_groups_available={g:sum(r['source_groups_available'][g] for r in selected) for g in GROUPS},
                    components={f:dict(available=sum(r['components'][f]['status']=='available_source_component' for r in selected),
                        issues=dict(Counter(i for r in selected for i in r['components'][f]['issues']))) for f in fields},actual_fcff_complete=0)
    return dict(protocol_id=protocol['protocol_id'],scope=dict(securities=len(samples),periods=periods,evaluation_as_of=protocol['evaluation_as_of'],database='none_research_source_snapshot'),
                summary=summary(rows),by_period={p:summary([r for r in rows if r['period']==p]) for p in periods},results=rows,
                classification_gaps=['operating_vs_nonoperating_working_capital','operating_income_tax_and_interest_tax_shield','acquisitions_and_noncash_reinvestment','depreciation_operating_scope'],
                boundary='源分量可用不等于标准事实、原文逐公司审核或经营分类闭合；余额差不等于现金流营运调节；各项税费不等于经营所得税；不得将缺项归零或生成实际FCFF')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('protocol',type=Path);parser.add_argument('snapshot',type=Path);args=parser.parse_args()
    try:
        raw=args.protocol.read_bytes();data=args.snapshot.read_bytes();source=json.loads(data)
        digest=lambda b:hashlib.sha256(b).hexdigest()
        if source['study_sha256']!=digest(raw):raise ValueError('source protocol hash differs')
        result=audit(json.loads(raw),source)
        result['evidence']=dict(source_adapter_sha256=hashlib.sha256(Path(__file__).with_name('tdx_research_source.py').read_bytes()).hexdigest(), protocol_sha256=digest(raw),snapshot_sha256=digest(data),code_sha256=digest(Path(__file__).read_bytes()),
                               shared_parser_sha256=digest(Path(__file__).with_name('backtest_tdx_history.py').read_bytes()))
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps(dict(status='rejected',reason=str(exc)),ensure_ascii=False));raise SystemExit(1)


if __name__=='__main__':main()
