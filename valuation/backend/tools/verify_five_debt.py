"""五家两年合并债务源值与非流动债券空白；到期债券反例独立保留。"""
from decimal import Decimal, ROUND_HALF_UP
import gzip
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
from tools.backtest_tdx_history import at, available, value

DIRECTORY=Path(__file__).resolve().parents[2]/'research/tdx-capital-inputs/five-debt'
CODES=('000417','000422','000521','000546','000599')
MONEY=re.compile(r'[0-9,]+\.[0-9]{2}')


def load_inputs():
    ledger=json.loads((DIRECTORY/'evidence.json').read_bytes())
    raw=gzip.decompress((DIRECTORY/'source.json.gz').read_bytes())
    if hashlib.sha256(raw).hexdigest()!=ledger['source_sha256']:raise ValueError('source hash differs')
    return ledger,json.loads(raw)


def verify(ledger,source):
    if [(r['code'],r['year']) for r in ledger['reports']]!=[(c,y) for c in CODES for y in (2021,2022)]:
        raise ValueError('fixed sample/period set differs')
    results=[]
    for report in ledger['reports']:
        code,year=report['code'],report['year'];path=DIRECTORY/report['file']
        if hashlib.sha256(path.read_bytes()).hexdigest()!=report['sha256']:raise ValueError('PDF hash differs')
        pages=subprocess.check_output(['pdftotext','-layout',str(path),'-'],text=True).split('\f')
        clean=lambda s:re.sub(r'\s+','',s)
        if code not in pages[report['identity_page']-1] or f'{year}年年度报告' not in clean(''.join(pages[:2])):
            raise ValueError('PDF identity/year differs')
        head=clean(pages[report['statement_page']-1])
        if '1、合并资产负债表' not in head or '单位：元' not in head or f'{year}年12月31日' not in head:
            raise ValueError('statement scope/period/unit differs')
        body='\n'.join(pages[report['statement_page']-1:]).split('1、合并资产负债表',1)[1].split('2、母公司资产负债表',1)[0]
        if [r['field'] for r in report['rows']]!=['FN41','FN55','FN52','FN439','FN56']:raise ValueError('field set differs')
        selected={}
        for r in report['rows']:
            matches=[l for l in body.splitlines() if clean(l).startswith(r['label'])]
            if len(matches)!=1 or clean(matches[0])!=clean(r['line']):raise ValueError('PDF row differs')
            selected[r['field']]=matches[0]
        rows=[r for r in source['records'] if r['code']==code and r['period']==f'{year}-12-31']
        if len(rows)!=1:raise ValueError('source identity differs')
        r=rows[0];arts=[a for a in source['artifacts'] if a['file']==r['artifact']]
        if len(arts)!=1 or arts[0]['report_period']!=r['period'] or available(r,arts[0])>at('2023-09-01T00:00:00+08:00'):
            raise ValueError('source period/cutoff differs')
        refs=[]
        for field in ('FN41','FN55','FN52','FN439'):
            amount=Decimal(MONEY.findall(selected[field])[0].replace(',',''))
            multiplier=10000 if field=='FN439' else 1
            encoded=amount if multiplier==1 else (amount/multiplier).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
            if struct.unpack('<I',struct.pack('<f',float(encoded)))[0]!=r['bits'][field]:raise ValueError('source bits differ')
            refs.append(dict(field=field,pdf_cny=str(amount),source_cny=str(value(r,field)*multiplier),bits=r['bits'][field]))
        # The 2021 Yihua bond amount is in the prior comparative column.
        # Right-aligned money columns share the long-borrowing row's first-column endpoint.
        end=list(MONEY.finditer(selected['FN55']))[0].end()
        bond_values=[Decimal(m.group().replace(',','')) for m in MONEY.finditer(selected['FN56']) if m.end()<=end+2]
        if any(v!=0 for v in bond_values) or r['bits']['FN56']!=0:
            raise ValueError('noncurrent bond blank/source zero differs')
        bond=clean(pages[report['bond_page']-1]);current_bonds=None
        if code=='000422' and year==2021:
            if '第九节债券相关情况√适用□不适用' not in bond or 'MTN001' not in bond:
                raise ValueError('current bond applicability differs')
            note=pages[194]
            amounts=[]
            for label in ('一年内到期的长期借款','一年内到期的应付债券','一年内到期的长期应付款','一年内到期的租赁负债','应付利息','合计'):
                lines=[l for l in note.split('其他说明',1)[0].splitlines() if clean(l).startswith(label)]
                if len(lines)!=1:raise ValueError('current maturity row differs')
                amounts.append(Decimal(MONEY.findall(lines[0])[0].replace(',','')))
            current_bonds=amounts[1]
            if current_bonds!=Decimal('20000000.00') or sum(amounts[:-1])!=amounts[-1] or amounts[-1]!=Decimal(next(x['pdf_cny'] for x in refs if x['field']=='FN52')):
                raise ValueError('current bond/maturity sum differs')
        elif '第九节债券相关情况' not in bond or not any(x in bond for x in ('□适用√不适用','□适用\uf052不适用')):
            raise ValueError('bond applicability differs')
        results.append(dict(code=code,period=r['period'],artifact=r['artifact'],source_matches=refs,
            noncurrent_bond_status=('source_zero_matches_printed_zero' if bond_values else 'source_zero_matches_blank_current_statement_column'),
            bond_section='applicable_current_maturity' if current_bonds is not None else 'not_applicable',
            confirmed_current_bond_cny=str(current_bonds) if current_bonds is not None else None,
            full_debt=None,economic_roic=None))
    return dict(results=results,nonzero_matches=sum(len(r['source_matches']) for r in results),
        boundary='later_acquired_research_only; statement_noncurrent_blank_not_absence_of_all_bonds; '
        'no_standard_zero_override; full_debt_and_operating_capital_not_closed')


if __name__=='__main__':
    print(json.dumps(verify(*load_inputs()),ensure_ascii=False,indent=2))
