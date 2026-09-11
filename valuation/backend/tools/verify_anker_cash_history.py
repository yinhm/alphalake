"""安克2024历史现金输入：三期合并原文、单季源位和资本开支累计口径。"""
import argparse
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import struct
import zipfile

from pypdf import PdfReader
from tools.backtest_tdx_history import value


def verify(directory,full_archive=None):
    ledger=json.loads((directory/'evidence.json').read_text());reports=ledger['reports']
    if ledger['code']!='300866' or ledger['org_id']!='gfbj0839473' or [r['period'] for r in reports]!=['2024-06-30','2024-09-30','2024-12-31']:raise ValueError('unsupported identity/report set')
    if [{k:v for k,v in r.items() if k!='rows'} for r in reports]!=json.loads((directory/'reports.json').read_text()):raise ValueError('report metadata differs')
    raw=(directory/'catalogue.json').read_bytes();request=json.loads((directory/'catalogue-request.json').read_text())
    if hashlib.sha256(raw).hexdigest()!=request['sha256']:raise ValueError('catalogue hash differs')
    catalogue=json.loads(raw);source=json.loads((directory/'source-snapshot.json').read_text())
    if source['study_sha256']!=hashlib.sha256((directory/'source-protocol.json').read_bytes()).hexdigest():raise ValueError('source protocol differs')
    index={(r['code'],r['period']):r for r in source['records']}
    if len(index)!=len(source['records']):raise ValueError('duplicate source identity')
    packages=json.loads((directory/'packages.json').read_text());results=[];source_values=[]
    for report,package in zip(reports,packages,strict=True):
        period=report['period'];name='gpcw'+period.replace('-','')+'.zip'
        if package['file']!=name:raise ValueError('package period differs')
        artifacts=[a for a in source['artifacts'] if a['file']==name]
        if len(artifacts)!=1 or artifacts[0]['report_period']!=period or any(package[k]!=artifacts[0][k] for k in ('sha256','md5','fetched_at')):raise ValueError('source/package metadata differs')
        raw=(directory/name).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=package['sample_sha256']:raise ValueError('sample package hash differs')
        with zipfile.ZipFile(directory/name) as z:dat=z.read(name.replace('.zip','.dat'))
        size=struct.unpack_from('<I',dat,12)[0]
        if len(dat)!=31+size or struct.unpack_from('<H',dat,6)[0]!=1 or dat[20:26]!=b'300866' or struct.unpack_from('<I',dat,27)[0]!=31:raise ValueError('sample layout differs')
        if size!=package['report_size'] or struct.unpack_from('<I',dat,2)[0]!=int(period.replace('-','')):raise ValueError('sample report header differs')
        record=dat[31:]
        if hashlib.sha256(record).hexdigest()!=package['record_sha256']:raise ValueError('record hash differs')
        if full_archive:
            original=(full_archive/name).read_bytes()
            if hashlib.sha256(original).hexdigest()!=package['sha256'] or hashlib.md5(original).hexdigest()!=package['md5']:raise ValueError('full package hash differs')
            with zipfile.ZipFile(full_archive/name) as z:full=z.read(z.namelist()[0])
            found=[]
            for i in range(struct.unpack_from('<H',full,6)[0]):
                code,marker,offset=struct.unpack_from('<6sBI',full,20+11*i)
                if code==b'300866':found.append(full[offset:offset+size])
            if found!=[record]:raise ValueError('sample/full record differs')
        matches=[a for a in catalogue['announcements'] if a['announcementId']==report['announcement_id']]
        if len(matches)!=1:raise ValueError('announcement not unique')
        a=matches[0]
        if a['secCode']!=ledger['code'] or a['orgId']!=ledger['org_id'] or report['url']!='https://static.cninfo.com.cn/'+a['adjunctUrl'] or '摘要' in a['announcementTitle']:raise ValueError('announcement identity differs')
        published=datetime.fromtimestamp(a['announcementTime']/1000,timezone(timedelta(hours=8))).date()
        row=index[(ledger['code'],period)]
        if value(row,'FN314')!=int(published.strftime('%y%m%d')):raise ValueError('FN314/original publication differs')
        raw=(directory/report['file']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=report['sha256']:raise ValueError('PDF hash differs')
        reader=PdfReader(directory/report['file']);texts={}
        def text(page):
            if page not in texts:texts[page]=re.sub(r'\s+','',reader.pages[page-1].extract_text())
            return texts[page]
        if len(report['rows'])!=3:raise ValueError('three original rows required')
        amounts={}
        for item in report['rows']:
            header=text(item['header_page'])
            expected_columns='项目本期发生额上期发生额' if period=='2024-09-30' else '项目附注2024'+('年半年度2023年半年度' if period=='2024-06-30' else '年度2023年度')
            title={'2024-06-30':'2024年半年度报告','2024-09-30':'2024年第三季度报告','2024-12-31':'2024年年度报告'}[period]
            if title not in header or item['section'] not in header or '单位：元' not in header or expected_columns not in header or '安克创新科技股份有限公司' not in header:raise ValueError('statement scope/unit/columns differ')
            pattern=re.escape(item['label']+item['note'])+r'(-?[\d,]+\.\d{2})(-?[\d,]+\.\d{2})'
            matches=re.findall(pattern,text(item['page']))
            if len(matches)!=1 or [v.replace(',','') for v in matches[0]]!=item['values']:raise ValueError('PDF row values differ')
            amounts[item['key']]=Decimal(item['values'][0])
        if set(amounts)!={'revenue','ocf','capex'}:raise ValueError('row set differs')
        expected={'FN114':amounts['capex']}
        if results:
            for field,key in [('FN230','revenue'),('FN234','ocf')]:expected[field]=amounts[key]-Decimal(results[-1]['cumulative_cny'][key])
        for field,n in expected.items():
            bits=struct.unpack('<I',struct.pack('<f',float(n)))[0]
            if row['bits'][field]!=bits or struct.unpack_from('<I',record,(int(field[2:])-1)*4)[0]!=bits:raise ValueError('source/PDF bits differ: '+field)
            source_values.append(dict(period=period,field=field,pdf_amount=str(n),source_bits=bits,source_value=str(value(row,field)),source_minus_pdf=str(value(row,field)-n)))
        results.append(dict(period=period,published=published.isoformat(),available_from=(published+timedelta(days=1)).isoformat()+'T00:00:00+08:00',cumulative_cny={k:str(v) for k,v in amounts.items()}))
    return dict(code=ledger['code'],status='original_cash_history_verified_not_materialized',periods=results,source_values=source_values,
        boundary='7 verified source values: FN114 three YTD amounts and FN230/FN234 Q3/Q4 differences; H1 revenue/OCF are cumulative anchors, not independent Q2 verification; no PDF overwrite or FCFF claim')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path);p.add_argument('--full-archive',type=Path);args=p.parse_args()
    result=verify(args.directory,args.full_archive)
    result['evidence']={name:hashlib.sha256((args.directory/name).read_bytes()).hexdigest() for name in ('evidence.json','source-snapshot.json','packages.json','catalogue.json')}
    result['evidence']['code_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
