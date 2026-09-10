"""从四份PDF定位32个模型输入；空白/横杠保留未取得数值证明。"""
import json,re,struct,csv,hashlib,io
from pathlib import Path
from decimal import Decimal as D
from pypdf import PdfReader
root=Path(__file__).parent
facts={(f['field'],f['period']):f for f in json.loads((root/'inputs.json').read_text())}
texts={}
for s in json.loads((root/'sources.json').read_text()):
 p=root/s['file'];assert hashlib.sha256(p.read_bytes()).hexdigest()==s['sha256']
 texts[s['period']]='\n'.join(f'\nPDF_PAGE_{i}\n'+(p.extract_text() or '') for i,p in enumerate(PdfReader(p).pages,1))
assert '本公司的记账本位币为人民币' in re.sub(r'\s+','',texts['2026-06-30'])
rows=[]
def section(period,start,end):
 text=texts[period];a=re.search(start,text);assert a,(period,start)
 b=re.search(end,text[a.end():]);assert b,(period,end)
 return text[a.start():a.end()+b.start()],a.start()
def line(block,pattern):
 found=list(re.finditer(pattern,block,re.M));assert len(found)==1,(pattern,len(found))
 m=found[0];tokens=re.findall(r'(?<!\S)(?:-?\d[\d,]*\.\d{2}|-)(?!\S)',m.group())
 return m,tokens
def add(field,period,pdf_period,offset,label,value,method='reported_current_column'):
 f=facts[field,period];assert f['code']=='920000' and f['instrument_id']==52135
 assert f['unit']==('share' if field=='FN238' else 'CNY')
 value=None if value is None else D(value.replace(',',''))
 status='pdf_blank_or_dash_not_zero_proof'
 if value is not None:
  bits=struct.unpack('<I',struct.pack('<f',float(value/D(f['multiplier']))))[0]
  assert bits==f['bits'],(field,period,value,bits,f['bits'],f['value'])
  status='pdf_numeric_matches_source_float32'
 page=re.findall(r'PDF_PAGE_(\d+)',texts[pdf_period][:offset])[-1]
 rows.append(dict(field=field,period=period,pdf_period=pdf_period,pdf_page=page,label=label,pdf_value='' if value is None else str(value),source_value=f['value'],source_bits=f['bits'],status=status,method=method))
labels={'FN86':r'^三[、.]营业利润.*$','FN305':r'^(?:其中：)?利息费用\s.*$','FN306':r'^利息收入\s.*$','FN83':r'^投资收益.*$','FN82':r'^公允价值变动收益.*$','FN301':r'^资产处置收益.*$'}
revenues={}
for period in ('2025-06-30','2025-12-31','2026-03-31','2026-06-30'):
 block,offset=section(period,r'[（(]三[）)]\s*合并利润表',r'[（(]四[）)]\s*母公司利润表')
 assert '单位：元' in block and ('2025' if period.startswith('2025') else '2026') in block[:160]
 m,t=line(block,r'^其中：营业收入\s.*$');assert len(t)==2
 revenues[period]=(D(t[0].replace(',','')),offset+m.start())
 if period=='2026-03-31':continue
 for field,pattern in labels.items():
  # 主表先有空白利息收入行，金额列的财务费用子项另行定位。
  if field=='FN306':pattern=r'^利息收入\s+-?\d[\d,]*\.\d{2}.*$'
  m,t=line(block,pattern);assert len(t)==2,(period,field,t)
  add(field,period,period,offset+m.start(),m.group(),None if t[0]=='-' else t[0])
block,offset=section('2025-12-31',r'2025\s*年分季度主要财务数据',r'季度数据与已披露')
assert '单位：元' in block
m,t=line(block,r'^营业收入\s.*$');assert len(t)==4
for period,value in zip(('2025-03-31','2025-06-30','2025-09-30','2025-12-31'),t):add('FN230',period,'2025-12-31',offset+m.start(),'分季度主要财务数据：营业收入',value,'reported_single_quarter_column')
assert sum(D(x.replace(',','')) for x in t[:2])==revenues['2025-06-30'][0]
for period in ('2026-03-31','2026-06-30'):
 value,offset=revenues[period]
 if period.endswith('06-30'):value-=revenues['2026-03-31'][0]
 add('FN230',period,period,offset,'合并营业收入',str(value),'Q1_reported' if period.endswith('03-31') else 'H1_minus_Q1_decimal')
period='2026-06-30'
block,offset=section(period,r'[（(]一[）)]\s*合并资产负债表',r'[（(]二[）)]\s*母公司资产负债表')
assert '单位：元' in block and '2026 年6 月30 日' in block[:160]
for field,label in {'FN41':'短期借款','FN52':'一年内到期的非流动负债','FN55':'应付债券','FN56':'长期借款','FN439':'租赁负债','FN69':'少数股东权益'}.items():
 m,t=line(block,r'^'+label+r'\s.*$')
 add(field,period,period,offset+m.start(),m.group(),t[0] if t and t[0]!='-' else None)
block,offset=section(period,r'[（(]五[）)]\s*合并现金流量表',r'[（(]六[）)]\s*母公司现金流量表')
assert '单位：元' in block
m,t=line(block,r'^六、期末现金及现金等价物余额.*$');assert len(t)==2
add('FN133',period,period,offset+m.start(),m.group(),t[0])
block,offset=section(period,r'第五节\s*股份变动和融资',r'股本结构变动情况')
assert '单位：股' in block and '期末' in block
m,_=line(block,r'^\s*总股本\s.*$');t=re.findall(r'\d{1,3}(?:,\d{3})+',m.group());assert len(t)==2
add('FN238',period,period,offset+m.start(),m.group(),t[-1],'ordinary_share_structure_ending_count')
assert len(rows)==32 and len({(x['field'],x['period']) for x in rows})==32
buffer=io.StringIO(newline='')
w=csv.DictWriter(buffer,fieldnames=list(rows[0]),lineterminator='\n');w.writeheader();w.writerows(rows)
assert buffer.getvalue().encode()==(root/'input-check.csv').read_bytes(),'PDF/input ledger changed'
print(json.dumps(dict(inputs=len(rows),numeric=sum(x['status']=='pdf_numeric_matches_source_float32' for x in rows),non_numeric=[(x['field'],x['period']) for x in rows if x['pdf_value']=='']),ensure_ascii=False))
