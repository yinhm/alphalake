"""固定利润模型与报表现金代理对照；不是FCFF实际值或模型选择。"""
import argparse
from collections import Counter
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import struct
from statistics import mean,median

from tools.backtest_tdx_history import at,available,window,quarter_periods
from tools.tdx_research_source import source_value as value,financial_value,source_field
from tools.backtest_tdx_origins import study,fit_calibration,evaluate

MODELS=('current_rule','bias_half')


def verify_anomaly(ledger,directory,source):
    from pypdf import PdfReader
    amounts={}
    for report in ledger['reports']:
        raw=(Path(directory)/report['file']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=report['sha256']:raise ValueError('anomaly PDF hash differs')
        pdf=PdfReader(Path(directory)/report['file'])
        normalized=lambda page:re.sub(r'\s+','',pdf.pages[page-1].extract_text())
        if ledger['code'] not in normalized(1):raise ValueError('anomaly company differs')
        header=normalized(report['header_page'])
        if not all(s in header for s in ('合并现金流量表','单位：元币种：人民币',report['header_period'])):
            raise ValueError('anomaly statement scope, unit or period differs')
        label='购建固定资产、无形资产和其他长期资产支付的现金'
        matches=re.findall(re.escape(label)+r'([0-9,]+\.[0-9]{2})([0-9,]+\.[0-9]{2})',normalized(report['row_page']))
        if len(matches)!=1 or [v.replace(',','') for v in matches[0]]!=report['values']:
            raise ValueError('anomaly PDF row/columns differ')
        amount=Decimal(report['values'][0]);amounts[report['period']]=amount
        records=[r for r in source['records'] if (r['code'],r['period'])==(ledger['code'],report['period'])]
        if len(records)!=1 or records[0]['bits'][ledger['field']]!=struct.unpack('<I',struct.pack('<f',float(amount)))[0]:
            raise ValueError('anomaly TDX bits differ from original amount')
    raw_ttm=sum((value(next(r for r in source['records'] if (r['code'],r['period'])==(ledger['code'],p)),ledger['field'])*c
                 for p,c in [('2025-12-31',1),('2026-06-30',1),('2025-06-30',-1)]),Decimal(0))
    pdf_ttm=amounts['2025-12-31']+amounts['2026-06-30']-amounts['2025-06-30']
    if pdf_ttm>=0 or raw_ttm>=0:raise ValueError('negative TTM anomaly no longer reproduced')
    return dict(status='source_values_confirmed_comparability_unresolved',tdx_capex_ttm_cny=str(raw_ttm),pdf_capex_ttm_cny=str(pdf_ttm),actual_fcff=None)


def cash_actual(source,code,target,cutoff):
    periods=quarter_periods(target)
    required=set(periods)|{f'{target.year-1}-12-31',target.replace(year=target.year-1).isoformat()}
    artifacts={a['file']:a for a in source['artifacts']};rows={}
    for r in source['records']:
        if r['code']!=code or r['period'] not in required:continue
        if r['period'] in rows:raise ValueError('duplicate cash source period')
        a=artifacts[r['artifact']]
        if a['report_period']!=r['period']:raise ValueError('cash artifact period differs')
        if available(r,a)<=at(cutoff):rows[r['period']]=r
    ocf=sum((financial_value(rows[p],'operating_cash_flow') for p in periods),Decimal(0))
    capex,refs=window(rows,target,'capital_expenditure_cash')
    if capex<0:raise ValueError('negative cumulative cash capex requires source review')
    refs=[dict(period=p,coefficient=1,field=source_field('operating_cash_flow'),artifact=rows[p]['artifact']) for p in periods]+refs
    return dict(operating_cash_flow=float(ocf/Decimal(1000000)),capital_expenditure_cash=float(capex/Decimal(1000000)),
                reported_ocf_less_capex=float((ocf-capex)/Decimal(1000000)),source_inputs=refs)


def summarize(rows):
    valid=[r for r in rows if r['cash_status']=='evaluated']
    return dict(candidates=len(rows),cash_statuses=dict(Counter(r['cash_status'] for r in rows)),models={m:dict(n=len(valid),
        mean_absolute_distance_pct_revenue=mean(abs(r['cash_distance_pct_revenue'][m]) for r in valid) if valid else None,
        median_absolute_distance_pct_revenue=median(abs(r['cash_distance_pct_revenue'][m]) for r in valid) if valid else None,
        mean_signed_distance_pct_revenue=mean(r['cash_distance_pct_revenue'][m] for r in valid) if valid else None) for m in MODELS})


def diagnose(p,source):
    cash=p['cash_proxy']
    if (cash['operating_cash_flow_field'],cash['operating_cash_flow_period_basis'],cash['capital_expenditure_field'],cash['capital_expenditure_period_basis'],cash['use'])!=(source_field('operating_cash_flow'),'quarter',source_field('capital_expenditure_cash'),'ytd','diagnostic_only_not_model_selection'):
        raise ValueError('unsupported cash proxy definition')
    dev=study(p,source,'development')
    if p.get('fixed_validation_model')!='bias_half' or dev['selection']['model']!='bias_half':raise ValueError('frozen calibration selection differs')
    training={o:fit_calibration(p,source,o) for o in p['origins']}
    rows=evaluate(p,source,'holdout',p['origins'],MODELS,training)
    tax=p['base_policy']['tax_rate'];capital=p['base_policy']['sales_to_capital']
    for row in rows:
        row.update(cash_status='blocked_forecast',actual_fcff=None)
        if row['status']!='evaluated':continue
        # 政策FCFF沿用原引擎的零滞后收入/资本比，再投资不因利润校准变化。
        row['forecast_fcff_policy']={m:f['ebit']*(1-tax)-(f['revenue']-row['base']['revenue'])/capital for m,f in row['forecasts'].items()}
        try:
            origin=date.fromisoformat(row['origin']);target=origin.replace(year=origin.year+1)
            actual=cash_actual(source,row['code'],target,p['evaluation_as_of'])
            row.update(cash_status='evaluated',cash_actual=actual,cash_distance_pct_revenue={m:100*(f-actual['reported_ocf_less_capex'])/row['actual']['revenue'] for m,f in row['forecast_fcff_policy'].items()})
        except (ValueError,KeyError,ArithmeticError) as exc:row.update(cash_status='blocked_cash_inputs',cash_reason=str(exc))
    return dict(contract_version='tdx-cash-proxy-diagnostic-v1',protocol_id=p['protocol_id'],amount_unit='million_CNY',
        summary=summarize(rows),by_origin={o:summarize([r for r in rows if r['origin']==o]) for o in p['origins']},
        by_profit_scope={s:summarize([r for r in rows if r.get('profit_scope','not_evaluated')==s]) for s in ('no_financial_fields_detected','financial_fields_present','not_evaluated')},
        calibration_training={o:{k:v for k,v in t.items() if k!='results'} for o,t in training.items()},results=rows,
        boundary='报表经营现金流减资本开支不等于FCFF；距离混合预测误差与会计口径差异，不据此选择参数或宣称估值准确率；实际FCFF仍为空；三个利润窗口均已观察，非新增独立验证')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('protocol',type=Path);parser.add_argument('snapshot',type=Path);args=parser.parse_args()
    try:
        raw=args.protocol.read_bytes();data=args.snapshot.read_bytes();p=json.loads(raw);source=json.loads(data)
        digest=lambda b:hashlib.sha256(b).hexdigest()
        if source['study_sha256']!=digest(raw):raise ValueError('cash source protocol differs')
        result=diagnose(p,source)
        result['evidence']=dict(protocol_sha256=digest(raw),snapshot_sha256=digest(data),code_sha256=digest(Path(__file__).read_bytes()),
            dependencies={name:digest((Path(__file__).resolve().parents[1]/name).read_bytes()) for name in ('tools/backtest_tdx_history.py', 'tools/tdx_research_source.py','tools/backtest_tdx_origins.py','data_sources/alphalake.py','data_sources/alphalake_calibration.py')})
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps(dict(status='rejected',reason=str(exc)),ensure_ascii=False));raise SystemExit(1)


if __name__=='__main__':main()
