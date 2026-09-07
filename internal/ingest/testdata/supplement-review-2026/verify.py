"""61 项补充取值的固定范围审核与显式供给；不把附注改称 TDX 标准事实。"""
import csv
from datetime import datetime
from decimal import Decimal as D, ROUND_HALF_UP
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import zipfile

sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parent
CHAIN=ROOT.parent/'valuation-chain-2026'
ASOF='2026-09-06T00:00:00+00:00'


def read(path):
    with path.open() as f:return list(csv.DictReader(f))


def build():
    original=read(ROOT/'original.csv');rules=json.loads((ROOT/'rules.json').read_text())
    assert len(original)==61 and len({(r['code'],r['period'],r['item']) for r in original})==61
    assert set(rules)=={r['code']+'/'+r['item'] for r in original}
    facts=read(CHAIN/'facts.csv');index={(r['code'],r['period'],r['field']):r for r in facts}
    filing_index={(r['code'],r['announcement_id']):r for r in read(CHAIN/'filings.csv')}
    raw_records={}
    for period in {r['period'] for r in original}:
        path=CHAIN/('gpcw'+period.replace('-','')+'.zip')
        with zipfile.ZipFile(path) as archive:data=archive.read(archive.namelist()[0])
        field_count=struct.unpack_from('<I',data,12)[0]//4
        for n in range(struct.unpack_from('<H',data,6)[0]):
            pos=20+11*n;offset=struct.unpack_from('<I',data,pos+7)[0]
            raw_records[data[pos:pos+6].decode(),period]=(struct.unpack_from('<'+str(field_count)+'I',data,offset),hashlib.sha256(path.read_bytes()).hexdigest())
    documents={}
    aliases={'FY2025':'2025-12-31','H12025':'2025-06-30','H12026':'2026-06-30'}
    for code,company in [('300866','anker'),('600519','moutai')]:
        for r in read(ROOT.parent/(company+'-valuation-2026')/'reported.csv'):
            p,key=r['id'].split('/',1)
            if p=='H12025-comparison':continue
            documents[code,aliases.get(p,p),key]=r
    extra=json.loads((ROOT.parent/'anker-dcf-2026/evidence.json').read_text())
    for e in extra:
        for c in e['cells']:
            documents['300866','2026-06-30','extra_'+c['key']]=dict(value=c['value'],pdf_id=e['pdf_id'],pdf_page=e['pdf_page'],printed=c['printed'])
    face=next(e for e in extra if e['quote'].startswith('11,048,200 '))
    face_amount=''.join(re.search(r'(1,104,660,8)\n(00\.00)',face['quote']).groups()).replace(',','')
    documents['300866','2026-06-30','convertible_face']=dict(value=face_amount,pdf_id=face['pdf_id'],pdf_page=face['pdf_page'],printed=face_amount)
    output=[]
    for old in original:
        code,p,key=old['code'],old['period'],old['item'];rule=rules[code+'/'+key];doc=documents[code,p,key]
        assert D(doc['value'])==D(old['pdf_value']),(code,p,key,'original evidence changed')
        company='anker' if code=='300866' else 'moutai'
        pdf=ROOT.parent/(company+'-valuation-2026')/(doc['pdf_id']+'.pdf')
        sha=hashlib.sha256(pdf.read_bytes()).hexdigest()
        filing=filing_index[code,doc['pdf_id']]
        assert sha==filing['pdf_sha256'],(code,p,key,'archived filing PDF differs')
        assert filing['document_locator'].lower().endswith('/'+doc['pdf_id']+'.pdf')
        assert filing['report_period'] in ('',p),(code,p,key,'filing period differs')
        if not filing['report_period']:assert rule['scope']=='finance_subsidiary'
        assert rule['unit']==('CNY/share' if key=='extra_conversion_price' else 'CNY')
        available=filing['available_at'];assert datetime.fromisoformat(available)<=datetime.fromisoformat(ASOF)
        value=D(doc['value']);tdx_source=''
        bits,probe_sha=raw_records[code,p];matches=[]
        for scale in (1,10000):
            encoded=struct.unpack('<I',struct.pack('<f',float((value/scale).quantize(D('.01'),rounding=ROUND_HALF_UP))))[0]
            if encoded:matches.extend(f'FN{i+1}*{scale}' for i,b in enumerate(bits) if b==encoded)
        probe=';'.join(matches) if matches else ('zero_ambiguous' if not value else 'no_nonzero_match_not_proof_of_absence')

        if rule['route']=='tdx_standard':
            fact=index[code,p,rule['field']];mult=D(fact['multiplier'])
            assert struct.unpack('<I',struct.pack('<f',float((value/mult).quantize(D('.01'),rounding=ROUND_HALF_UP))))[0]==int(fact['bits'])
            assert fact['period_type']==('instant' if rule['period_basis']=='instant' else ('FY' if p.endswith('12-31') else 'H1'))
            value=D(fact['value']);tdx_source=fact['artifact_sha256']+'/'+fact['announcement_id']
        elif rule['route']=='cninfo_reported_zero':
            assert key=='dividends_receivable' and value==0 and doc['printed']=='-'
        else:assert rule['route']=='cninfo_note' and not rule['field']
        output.append(dict(code=code,period=p,item=key,value=str(value),unit=rule['unit'],period_basis=rule['period_basis'],
            route=rule['route'],field=rule['field'],scope=rule['scope'],reason=rule['reason'],
            pdf_value=doc['value'],pdf_locator=company+'/'+doc['pdf_id']+'.pdf#page='+str(doc['pdf_page']),pdf_sha256=sha,
            announcement_id=doc['pdf_id'],available_at=available,information_as_of=ASOF,tdx_source=tdx_source,tdx_probe=probe,tdx_probe_sha256=probe_sha))
    # 附注分量合计必须与独立标准总额的源精度一致；不由一个总额反推多个分量。
    notes={r['item']:D(r['value']) for r in output if r['code']=='300866' and r['period']=='2026-06-30'}
    for field,keys in [('FN52',['current_loans','current_bonds','current_leases']),
                       ('FN9',['extra_current_financial_debt','extra_current_financial_equity','trading_forward_asset']),
                       ('FN433',['extra_noncurrent_financial_debt','extra_noncurrent_financial_equity'])]:
        f=index['300866','2026-06-30',field]
        total=sum((notes[k] for k in keys),D(0))/D(f['multiplier'])
        assert struct.unpack('<I',struct.pack('<f',float(total.quantize(D('.01'),rounding=ROUND_HALF_UP))))[0]==int(f['bits']),(field,'note components vs standard total')
    assert sum(r['route']=='tdx_standard' for r in output)==27
    return output


def verify(write=False):
    rows=build();out=io.StringIO();w=csv.DictWriter(out,fieldnames=list(rows[0]),lineterminator='\n');w.writeheader();w.writerows(rows)
    if write:(ROOT/'resolved.csv').write_text(out.getvalue())
    else:assert (ROOT/'resolved.csv').read_text()==out.getvalue(),'supplement supply differs'
    print('通过：原 61 项取值全部逐项审核，27 项标准 TDX、33 项附注、1 项原文确认零；后三十四项仍非 TDX 独立供给。')


if __name__=='__main__':
    subprocess.run([sys.executable,str(CHAIN/'verify.py')],check=True)
