"""中国天楹两年债务源位、报表空白及融资附注核验；不写标准零值。"""
from decimal import Decimal, ROUND_HALF_UP
import gzip
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess

from tools.backtest_tdx_history import at, available
from tools.tdx_research_source import source_value as value

DIRECTORY = Path(__file__).resolve().parents[2]/'research/tdx-capital-inputs/cnty-debt'


def load_inputs():
    ledger=json.loads((DIRECTORY/'evidence.json').read_bytes())
    raw=gzip.decompress((DIRECTORY/'source.json.gz').read_bytes())
    if hashlib.sha256(raw).hexdigest()!=ledger['source_sha256']:
        raise ValueError('source hash differs')
    return ledger,json.loads(raw)


def verify(ledger,source):
    if ledger['code']!='000035' or [r['year'] for r in ledger['reports']]!=[2021,2022]:
        raise ValueError('company/period differs')
    reports={}
    for r in ledger['reports']:
        path=DIRECTORY/r['file']
        if hashlib.sha256(path.read_bytes()).hexdigest()!=r['sha256']:
            raise ValueError('PDF hash differs')
        pages=subprocess.check_output(['pdftotext','-layout',str(path),'-'],text=True).split('\f')
        compact=lambda n:re.sub(r'\s+','',pages[n-1])
        year=r['year']
        if (r['statement_page'],r['bonds_page']) != ((153,145) if year==2021 else (108,101)):
            raise ValueError('fixed statement scope differs')
        if '000035' not in compact(7 if year==2021 else 6) or f'中国天楹股份有限公司{year}年年度报告' not in compact(1):
            raise ValueError('PDF identity differs')
        if '1、合并资产负债表' not in compact(151 if year==2021 else 106):
            raise ValueError('consolidated scope differs')
        bond=compact(r['bonds_page'])
        if '第九节债券相关情况' not in bond or not any(x in bond for x in ('□适用√不适用','□适用\uf052不适用')):
            raise ValueError('bond applicability differs')
        lines=[re.sub(r'\s+','',l) for l in pages[r['statement_page']-1].splitlines()]
        if lines.count('应付债券')!=1 or any(l.startswith('应付债券') and l!='应付债券' for l in lines):
            raise ValueError('bond blank differs')
        header=compact(151 if year==2021 else 106)
        if '单位：元' not in header or f'{year}年12月31日' not in header:
            raise ValueError('statement unit/period differs')
        reports[year]=pages
    def extract(year,page,label):
        lines=[re.sub(r'\s+','',l) for l in reports[year][page-1].splitlines()]
        selected=[l[len(label):] for l in lines if l.startswith(label) and re.match(r'[0-9]',l[len(label):])]
        if len(selected)!=1:
            raise ValueError('PDF row not unique')
        return [s.replace(',','') for s in re.findall(r'[0-9,]+\.[0-9]{2}',selected[0])]
    amounts={}
    for row in ledger['rows']:
        vals=extract(2022,row['page'],row['label'])
        if vals!=row['values'] or row['key'] in amounts:
            raise ValueError('PDF amounts differ')
        amounts[row['key']]=list(map(Decimal,vals))
    ncl=('long','leases','payables','provisions','deferred_income','deferred_tax','other_noncurrent')
    for column in (0,1):
        if sum(amounts[k][column] for k in ncl)!=amounts['noncurrent_total'][column]:
            raise ValueError('noncurrent liability sum differs')
        if sum(amounts[k][column] for k in ('current_loans','current_payables','current_leases'))!=amounts['current'][column]:
            raise ValueError('current maturity sum differs')
        if amounts['financing_payables'][column]-amounts['current_payables'][column]!=amounts['payables'][column]:
            raise ValueError('financing classification differs')
    # Compare the 2021 original closing column with the 2022 opening comparative.
    for row in ledger['rows']:
        if row['key'] in (*ncl,'noncurrent_total','current'):
            if Decimal(extract(2021,153,row['label'])[0])!=amounts[row['key']][1]:
                raise ValueError('prior original/comparative differs')
    compact=lambda page:re.sub(r'\s+','',reports[2022][page-1])
    if '本集团支付义务对手方转移至上述融资的资金提供方' not in compact(191) or '保险合同准备金' not in compact(192):
        raise ValueError('financing/insurance note differs')
    refs=[]
    for year,col in ((2022,0),(2021,1)):
        matches=[r for r in source['records'] if r['code']=='000035' and r['period']==f'{year}-12-31']
        if len(matches)!=1: raise ValueError('source identity not unique')
        r=matches[0];artifacts=[a for a in source['artifacts'] if a['file']==r['artifact']]
        if len(artifacts)!=1 or artifacts[0]['report_period']!=r['period'] or available(r,artifacts[0])>at('2023-09-01T00:00:00+08:00'):
            raise ValueError('source cutoff/period differs')
        if r['bits']['FN56']!=0: raise ValueError('bond source zero differs')
        for field,key,multiplier in [('FN41','short',1),('FN55','long',1),('FN52','current',1),('FN439','leases',10000)]:
            amount=amounts[key][col]
            encoded=amount if multiplier==1 else (amount/10000).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
            if r['bits'][field]!=struct.unpack('<I',struct.pack('<f',float(encoded)))[0]:
                raise ValueError('source bits differ')
            refs.append(dict(period=r['period'],field=field,artifact=r['artifact'],source_bits=r['bits'][field],
                pdf_cny=str(amount),source_cny=str(value(r,field)*multiplier),source_multiplier=multiplier))
    return dict(code='000035',source_matches=refs,bond_periods=['2021-12-31','2022-12-31'],
        bond_status='zero_supported_by_blank_inapplicability_and_liability_reconciliation_not_printed_zero',
        additional_2022_financing_claims_cny=str(amounts['payables'][0]+amounts['third_party'][0]+amounts['supplier_finance'][0]),
        additional_claims_scope='noncurrent_financing_payables_plus_third_party_borrowings_plus_supplier_finance; current_financing_payables_already_in_FN52',
        full_debt=None,economic_roic=None,classified_reinvestment=None,
        boundary='two_FY_research_evidence_only; insurance_scope_unresolved; no_source_override_or_standard_mapping_extension')


if __name__=='__main__':
    print(json.dumps(verify(*load_inputs()),ensure_ascii=False,indent=2))
