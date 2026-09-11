"""六家两期FN52附注闭合；保留本息混列、空白与未分类应付款。"""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
from tools import verify_cnty_debt as cnty, verify_five_debt as five
from tools.backtest_tdx_history import at, available, value

DIRECTORY=five.DIRECTORY.parent


def load_inputs():
    reports={};records=[];artifacts=[]
    for module in (cnty,five):
        ledger,source=module.load_inputs()
        records+=source['records'];artifacts+=source['artifacts']
        for report in ledger['reports']:
            code=report.get('code','000035')
            reports[code,report['year']]=(module.DIRECTORY/report['file'],report['sha256'])
    return json.loads((DIRECTORY/'maturities.json').read_bytes()),reports,records,artifacts


def verify(ledger,reports,records,artifacts):
    if [(r['code'],r['year']) for r in ledger['reports']]!=[(c,y) for c in ('000035',*five.CODES) for y in (2021,2022)]:
        raise ValueError('fixed panel differs')
    results=[]
    for report in ledger['reports']:
        key=(report['code'],report['year']);path,sha=reports[key]
        if hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise ValueError('PDF hash differs')
        pages=subprocess.check_output(['pdftotext','-layout',str(path),'-'],text=True).split('\f')
        clean=lambda x:re.sub(r'\s+','',x)
        text='\n'.join(pages[n-1] for n in report['section_pages'])
        match=re.search(r'\d+[、.]\s*一年内到期的非流动负债',text)
        if not match or clean(match.group())!=report['section']:raise ValueError('note section differs')
        body=text[match.end():];observed=[]
        for line in body.splitlines():
            if five.MONEY.search(line) or clean(line).startswith(('一年内到期','限制性股票回购义务','合计')):
                observed.append(clean(line))
                if clean(line).startswith('合计'):break
        if observed!=[clean(r['line']) for r in report['rows']]:raise ValueError('note rows differ')
        amounts=[];components=[]
        for row in report['rows']:
            lines=[l for l in pages[row['page']-1].splitlines() if clean(l)==clean(row['line'])]
            if len(lines)!=1:raise ValueError('note row not unique')
            # Fixed statement layout: boundary between the current and comparative columns.
            # Re-derive it from a populated same-page row, not a mutable stored coordinate.
            peers=[r for r in report['rows'] if r['page']==row['page']]
            reference=next((r for r in peers if r['label']=='合计'),peers[0])
            reference_line=next(l for l in pages[reference['page']-1].splitlines() if clean(l)==clean(reference['line']))
            spans=list(five.MONEY.finditer(reference_line))
            if len(spans)!=2:raise ValueError('column anchors differ')
            boundary=(spans[0].end()+spans[1].start())//2
            if boundary!=row['column_end']:raise ValueError('column boundary differs')
            matches=[m.group().replace(',','') for m in five.MONEY.finditer(lines[0]) if m.end()<=boundary]
            amount=matches[0] if len(matches)==1 else None
            if len(matches)>1 or amount!=row['amount_cny']:raise ValueError('current amount differs')
            if amount is not None:amounts.append(Decimal(amount))
            if row['label']!='合计':
                components.append(dict(label=row['label'],amount_cny=amount,
                    status='blank_not_asserted_zero' if amount is None else 'reported_amount',
                    classification=('principal_interest_combined' if '本息' in row['label'] else
                                    'explicit_interest' if '利息' in row['label'] else
                                    'payable_nature_unresolved' if '长期应付款' in row['label'] and amount not in (None,'0.00') else 'reported_component')))
        total=Decimal(report['rows'][-1]['amount_cny'])
        if sum(amounts[:-1])!=total:raise ValueError('reported component sum differs')
        source=[r for r in records if r['code']==key[0] and r['period']==f'{key[1]}-12-31']
        if len(source)!=1:raise ValueError('source identity differs')
        source=source[0]
        # Both parent subsets contain the same two archive metadata entries.
        arts=[a for a in artifacts if a['file']==source['artifact']]
        if not arts or any(a!=arts[0] for a in arts) or arts[0]['report_period']!=source['period'] or available(source,arts[0])>at('2023-09-01T00:00:00+08:00'):
            raise ValueError('source cutoff/period differs')
        if struct.unpack('<I',struct.pack('<f',float(total)))[0]!=source['bits']['FN52']:
            raise ValueError('FN52 source bits differ')
        results.append(dict(code=key[0],period=source['period'],components=components,
            printed_component_sum_cny=str(total),source_cny=str(value(source,'FN52')),source_bits=source['bits']['FN52'],
            fully_classified_principal=None,full_debt=None))
    return dict(results=results,closed_statement_totals=len(results),
        boundary='printed_amount_sum_not_blank_zero_imputation; mixed_interest_and_payable_nature_preserved; '
        'no_full_debt_operating_capital_or_growth_estimate; later_acquired_not_strict_PIT')


if __name__=='__main__':
    print(json.dumps(verify(*load_inputs()),ensure_ascii=False,indent=2))
