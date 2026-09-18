"""两家开发异常的原文/源值/披露时点核验，不据此删除历史失败样本。"""
import argparse
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import struct

from pypdf import PdfReader
from tools.backtest_tdx_history import at, available
from tools.tdx_research_source import financial_value


def verify(ledger,directory,source):
    years={'000809':range(2022,2026),'688443':range(2024,2026)}.get(ledger['code'])
    if years is None or [r['period'] for r in ledger['reports']]!=[f'{y}-06-30' for y in years]:raise ValueError('unsupported company/period scope')
    raw=(directory/'catalogue.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=ledger['catalogue_sha256']:raise ValueError('catalogue hash differs')
    catalogue=json.loads(raw)['announcements'];artifacts={a['file']:a for a in source['artifacts']};results=[]
    for report in ledger['reports']:
        path=directory/report['file']
        if hashlib.sha256(path.read_bytes()).hexdigest()!=report['sha256']:raise ValueError('PDF hash differs')
        matches=[a for a in catalogue if a['announcementId']==report['announcement_id']]
        if len(matches)!=1:raise ValueError('catalogue identity not unique')
        a=matches[0];year=int(report['period'][:4])
        if a['secCode']!=ledger['code'] or a['orgId']!=ledger['org_id'] or a['announcementTitle']!=f'{year}年半年度报告' or 'https://static.cninfo.com.cn/'+a['adjunctUrl']!=report['url']:raise ValueError('catalogue identity/URL differs')
        publication=datetime.fromtimestamp(a['announcementTime']/1000,timezone.utc).astimezone(timezone(timedelta(hours=8)))
        usable=publication.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1);cutoff=at(f'{year}-09-01T00:00:00+08:00')
        if usable>cutoff:raise ValueError('original not available by forecast cutoff')
        pdf=PdfReader(path);pages={}
        def text(page):
            if page not in pages:pages[page]=re.sub(r'\s+','',pdf.pages[page-1].extract_text()).replace(':','：')
            return pages[page]
        identity_page=6 if ledger['code']=='000809' else 1
        if ledger['code'] not in text(identity_page) or report['issuer'] not in text(identity_page) or f'{year}年半年度报告' not in text(1):raise ValueError('PDF company/period differs')
        amounts={}
        for row in report['rows']:
            body=text(row['page'])
            if row['section'] not in body or '单位：元' not in body or row['header_period'] not in body:raise ValueError('statement scope/unit/period differs')
            selected=body.split(row['section'],1)[1]
            matches=re.findall(re.escape(row['label'])+r'((?:[0-9,]+\.[0-9]{2})+)',selected)
            if len(matches)!=1:raise ValueError('PDF row not unique')
            values=[s.replace(',','') for s in re.findall(r'[0-9,]+\.[0-9]{2}',matches[0])]
            if values!=row['values']:raise ValueError('PDF row values differ')
            amounts[row['key']]=Decimal(values[0])
        if ledger['code']=='688443':
            subtotal=amounts['other_business_income']+(amounts['main_business_income'] if year==2025 else Decimal(0))
            if subtotal!=amounts['revenue']:raise ValueError('income breakdown differs')
        refs=[];half_revenue=Decimal(0);bound=Decimal('.01')
        for month in ('03-31','06-30'):
            period=f'{year}-{month}';records=[r for r in source['records'] if (r['code'],r['period'])==(ledger['code'],period)]
            if len(records)!=1:raise ValueError('source identity not unique')
            record=records[0];artifact=artifacts[record['artifact']]
            if artifact['report_period']!=period or available(record,artifact)>cutoff:raise ValueError('source period/cutoff differs')
            revenue=financial_value(record,'revenue');bits=record['bits']['FN230'];half_revenue+=revenue;bound+=Decimal(2)**(max(((bits>>23)&255)-127,-126)-24)
            refs.append(dict(period=period,field='FN230',source_bits=bits,artifact=record['artifact']))
            if month=='06-30':
                inventory=financial_value(record,'inventories')
                if record['bits']['FN17']!=struct.unpack('<I',struct.pack('<f',float(amounts['inventory'])))[0]:raise ValueError('inventory source bits differ')
                refs.append(dict(period=period,field='FN17',source_bits=record['bits']['FN17'],artifact=record['artifact']))
        if abs(half_revenue-amounts['revenue'])>bound:raise ValueError('revenue exceeds source rounding bound')
        for note in report['notes']:
            if any(anchor not in text(note['page']) for anchor in note['anchors']):raise ValueError('business evidence anchor differs')
        results.append(dict(period=report['period'],published_date=publication.date().isoformat(),original_available_from=usable.isoformat(),forecast_as_of=cutoff.isoformat(),
            pdf_inventory_cny=str(amounts['inventory']),source_inventory_cny=str(inventory),pdf_half_revenue_cny=str(amounts['revenue']),source_half_revenue_cny=str(half_revenue),revenue_rounding_bound_cny=str(bound),
            source_inventory_minus_pdf_cny=str(inventory-amounts['inventory']),source_half_revenue_minus_pdf_cny=str(half_revenue-amounts['revenue']),source_inputs=refs,notes=report['notes']))
        if ledger['code']=='688443':results[-1]['income_note_cny']={k:str(v) for k,v in amounts.items() if k.endswith('_business_income')}
    return dict(code=ledger['code'],periods=results,status='source_values_confirmed_historical_business_and_scope_break' if ledger['code']=='000809' else 'source_values_confirmed_early_commercialization_base',
        original_failed_samples_retained=True,actual_fcff=None,boundary='later-acquired original evidence; no strict PIT, automatic exclusion or full TTM revenue/target cash audit; no retrospective adoption')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('ledger',type=Path);parser.add_argument('snapshot',type=Path);args=parser.parse_args()
    try:
        raw=args.snapshot.read_bytes();data=args.ledger.read_bytes();ledger=json.loads(data)
        if hashlib.sha256(raw).hexdigest()!=ledger['snapshot_sha256']:raise ValueError('snapshot hash differs')
        result=verify(ledger,args.ledger.parent,json.loads(raw));result['evidence']=dict(ledger_sha256=hashlib.sha256(data).hexdigest(),snapshot_sha256=ledger['snapshot_sha256'],code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,KeyError,OSError,IndexError) as exc:
        print(json.dumps(dict(status='rejected',reason=str(exc)),ensure_ascii=False));raise SystemExit(1)


if __name__=='__main__':main()
